#!/usr/bin/env python3
"""CHƯƠNG TRÌNH CHẠY TRÊN MÁY TÍNH CỦA BẠN (Windows / macOS / Linux).

Mặc định dùng detector OpenCV cổ điển đã kiểm chứng trên cốc trong suốt có backlight.
Tùy chọn --yolo dùng YOLO tìm hộp cốc + CV đo vạch nước. Bơm trên PC là DRY-RUN.

Cài đặt một lần:
    pip install opencv-python numpy ultralytics
    (train tuỳ chọn: xem tools/train_yolo.py)

Chạy:
    python3 tools/run_pc.py                 # camera mặc định 0
    python3 tools/run_pc.py --camera 1      # camera khác
    python3 tools/run_pc.py --video cup.mp4 # chạy trên file video
    python3 tools/run_pc.py --synthetic     # không cần camera
    python3 tools/run_pc.py --yolo          # bật YOLO cho hộp cốc (model COCO/custom)

Chế độ ESP32 (đặt cốc → PC bật camera → nhận diện → gửi CUP_OK → mở khoá nút/mic):
    python3 tools/run_pc.py --port sim --synthetic    # thử không cần gì cả
    python3 tools/run_pc.py --port COM5               # ESP32 thật (Windows)
    python3 tools/run_pc.py --port /dev/ttyUSB0       # ESP32 thật (Linux/macOS)
Trong chế độ này camera TỰ BẬT khi ESP báo có cốc và TỰ TẮT khi rảnh, nút bấm trên
ESP32 chỉ hoạt động sau khi PC xác nhận cốc (đúng yêu cầu thiết kế). Thêm --classic
nếu muốn chạy kiểu cũ (không cần ESP32).

Phím:
    1..5   chọn mức rót (100/150/200/250/300 ml)
    s      bắt đầu rót          e      dừng
    t      CHỤP khung hình hiện tại vào datasets/cups/user/images/  (ảnh train sau này)
    d      bật/tắt vẽ hộp YOLO   q      thoát
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.config import load_config                     # noqa: E402
from cupfiller.synthetic import SyntheticCupCamera, CupScene  # noqa: E402
from cupfiller.camera import open_camera                     # noqa: E402
from cupfiller.pump import open_pump                         # noqa: E402
from cupfiller.controller import FillController              # noqa: E402
from cupfiller.detection import CupDetector, draw_overlay    # noqa: E402
from cupfiller.yolo_dataset import DATASET_DIR               # noqa: E402

SAVE_DIR = os.path.join(DATASET_DIR, "user", "images")


def make_detector(cfg, args):
    # Với cốc trong suốt + backlight, detector cổ điển được kiểm chứng ổn định hơn
    # YOLO COCO tổng quát. Chỉ bật YOLO khi người dùng yêu cầu --yolo.
    if not args.yolo or args.classical:
        return CupDetector(cfg), "OpenCV co dien (can backlight)"
    try:
        from cupfiller.detection_yolo import YoloCupDetector, available_weights

        det = YoloCupDetector(cfg, weights=args.weights)
        return det, "YOLO hybrid: " + os.path.basename(available_weights(args.weights))
    except ImportError as ex:
        print("[warn] thieu ultralytics (%s) -> dung detector co dien" % ex)
        return CupDetector(cfg), "co dien (backlight)"


def run_auto(cfg, args, det, det_name, scene):
    """CHẾ ĐỘ ESP32: camera chỉ mở khi cảm biến báo có cốc; nút/mic do ESP32 giữ khoá."""
    from cupfiller.session import AutoFillSession

    sess = AutoFillSession(cfg, port=args.port, detector=det, scene=scene,
                           on_state=lambda name, fields: None)
    dt = 1.0 / max(1.0, float(cfg.get("control.loop.fps", 15)))
    show_boxes = True
    print("Chế độ ESP32 : %s" % args.port)
    print("Detector     : %s" % det_name)
    print("Luồng        : đặt cốc → ESP báo PC → BẬT CAMERA → nhận diện → CUP_OK → ")
    print("               mở khoá nút/mic → bấm nút (hoặc nói) → bơm → nhấc cốc")
    print("Phím         : 1..5 chọn mức qua PC | s rót | e dừng | t chụp ảnh | q thoát")
    while True:
        t0 = time.time()
        sess.tick(dt)
        tel = sess.telemetry()
        esp = tel.get("esp") or {}
        frame = sess.frame
        if frame is None:
            # không có camera (chưa có cốc hoặc --esp.dry_run): vẫn hiện bảng trạng thái
            os.system("cls" if os.name == "nt" else "clear")
            print("=== MÁY RÓT NƯỚC — chế độ ESP32 ===")
            print("ESP  : %s | cốc=%s | PC xác nhận=%s | bơm=%s | %s/%s ml"
                  % (esp.get("state_name"), _yn(esp.get("cup_present")),
                     _yn(esp.get("pc_confirmed")), _yn(esp.get("pumping")),
                     esp.get("poured_ml"), esp.get("target_ml")))
            print("PC   : %-16s %s" % (tel.get("state"), tel.get("message")))
            print("Cốc  : %s" % (tel.get("cup_meta") or "chưa nhận diện"))
            if tel.get("error"):
                print("LỖI  : %s" % tel["error"])
            key = cv2.waitKey(100) & 0xFF
            if key == ord("q"):
                break
            if ord("1") <= key <= ord("5"):
                sess.press_preset(key - ord("1"))
            elif key == ord("s"):
                sess.start_from_pc()
            elif key == ord("e"):
                sess.stop_from_pc()
            continue

        vis = draw_overlay(
            frame, sess.det,
            target_mm=(tel.get("vision") or {}).get("target_mm"),
            text=[
                "ESP: %s | xác nhận=%s | bơm=%s" % (esp.get("state_name"),
                                                    _yn(esp.get("pc_confirmed")),
                                                    _yn(esp.get("pumping"))),
                "PC : %s - %s" % (tel.get("state"), tel.get("message")),
                "cốc: %s" % (tel.get("cup_meta") or "chưa nhận diện"),
                tel.get("error") or "",
            ],
        )
        if show_boxes and hasattr(det, "last_boxes"):
            for (x0, y0, x1, y1, sc) in det.last_boxes:
                cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), (255, 200, 0), 2)
        cv2.imshow("may_rot_nuoc", vis)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif ord("1") <= key <= ord("5"):
            sess.press_preset(key - ord("1"))
        elif key == ord("s"):
            sess.start_from_pc()
        elif key == ord("e"):
            sess.stop_from_pc()
        elif key == ord("d"):
            show_boxes = not show_boxes
        took = time.time() - t0
        if took < dt:
            time.sleep(dt - took)
    sess.close()
    cv2.destroyAllWindows()


def _yn(value):
    return "có" if value else "không"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None,
                    help="cổng ESP32: sim | COM5 | /dev/ttyUSB0 | tcp://host:port")
    ap.add_argument("--classic", action="store_true",
                    help="chạy kiểu cũ (không dùng ESP32) dù có --port")
    ap.add_argument("--camera", default="0")
    ap.add_argument("--video", default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--yolo", action="store_true", help="bật YOLO để tìm hộp cốc")
    ap.add_argument("--classical", action="store_true", help="(cũ) ép dùng OpenCV cổ điển")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    scene = None
    if args.synthetic:
        cfg.set("camera.backend", "synthetic")
        cfg.set("control.pump.type", "sim")
        scene = SyntheticCupCamera(CupScene(), fps=float(cfg.get("control.loop.fps", 15)))
    elif args.video:
        cfg.set("camera.backend", "video")
        cfg.set("camera.source_file", args.video)
        cfg.set("control.pump.type", "manual")
    else:
        cfg.set("camera.backend", "v4l2")
        cfg.set("camera.device", int(args.camera) if args.camera.isdigit() else args.camera)
        cfg.set("control.pump.type", "manual")   # PC: dry-run, không bật bơm thật
    print("Dang mo camera; lan dau co the mat 1-3 giay...", flush=True)
    cam = open_camera(cfg, scene=scene)
    print("Da mo camera (%s). Dang khoi tao detector..." % getattr(cam, "backend_api", cam.backend), flush=True)
    pump = open_pump(cfg, getattr(cam, "synthetic", None))
    det, det_name = make_detector(cfg, args)

    if args.port and not args.classic:
        # chế độ ESP32: camera mở/đóng theo cảm biến cốc của ESP32
        if args.port == "sim":
            cfg.set("control.pump.type", "sim")
        print("Da mo camera se do ESP32 dieu khien (%s)..." % args.port)
        run_auto(cfg, args, det, det_name, scene)
        cam.release()
        return

    ctl = FillController(cfg, cam, pump, detector=det)

    os.makedirs(SAVE_DIR, exist_ok=True)
    dt = 1.0 / ctl.fps
    show_boxes = True
    print("Detector :", det_name)
    print("Phim     : 1..5 muc rot | s mo phong | e dung | t chup anh | d an/hien hop YOLO (chi --yolo) | q thoat")
    n_save = len(os.listdir(SAVE_DIR))
    while True:
        t0 = time.time()
        tel = ctl.tick(dt)
        frame = ctl.frame
        if frame is None:
            time.sleep(dt)
            continue
        vis = draw_overlay(
            frame,
            ctl.last_det,
            target_mm=tel["target_mm"] or None,
            text=[
                "[%s] preset %g ml" % (tel["state"], tel["preset_ml"]),
                "nuoc %.1f/%.1f mm  ~%.0f ml  pwm %.2f" % (tel["h_mm"], tel["target_mm"], tel["volume_ml"], tel["pwm"]),
                "det: %s" % det_name,
                tel["alert"],
            ],
        )
        if show_boxes and hasattr(det, "last_boxes"):
            for (x0, y0, x1, y1, sc) in det.last_boxes:
                cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), (255, 200, 0), 2)
                cv2.putText(vis, "cup %.2f" % sc, (int(x0), int(y0) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1)
        cv2.imshow("may_rot_nuoc", vis)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("s"):
            ctl.start()
        elif key == ord("e"):
            ctl.stop()
        elif key == ord("d"):
            show_boxes = not show_boxes
        elif key == ord("t"):
            n_save += 1
            p = os.path.join(SAVE_DIR, "pc_%04d.png" % n_save)
            cv2.imwrite(p, frame)
            print("  da luu anh train:", os.path.relpath(p, ROOT))
        elif ord("1") <= key <= ord("5"):
            i = key - ord("1")
            if i < len(ctl.presets):
                ctl.set_preset(ctl.presets[i])
                print("  preset ->", ctl.preset_ml, "ml")
        took = time.time() - t0
        if took < dt:
            time.sleep(dt - took)
    pump.off()
    cam.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
