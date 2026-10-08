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


def run_auto(cfg, args, det, det_name, scene, window=True):
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
        if not window:
            # Không màn hình (SSH / Pi không GUI): in một dòng trạng thái tại chỗ
            sys.stdout.write("\r%s | ESP %s | cốc %s | xác nhận %s | %s/%s ml | %s   "
                             % (tel.get("state"), esp.get("state_name"),
                                _yn(esp.get("cup_present")), _yn(esp.get("pc_confirmed")),
                                esp.get("poured_ml"), esp.get("target_ml"),
                                "BƠM" if esp.get("pumping") else "   "))
            sys.stdout.flush()
            time.sleep(dt)
            continue
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


def run_demo(cfg, args, det, det_name, scene):
    """Kịch bản tự chạy: không cần màn hình, không cần phần cứng.

    Dùng ESP32 giả lập + camera giả lập để chứng minh đúng luồng đã thiết kế:
        bấm nút khi CHƯA có cốc        -> ESP bỏ qua (nút còn khoá)
        đặt cốc -> ESP gửi CUP_PLACED  -> PC MỚI BẬT CAMERA + nhận diện cốc
        bấm nút khi CHƯA xác nhận      -> ESP vẫn bỏ qua (nút còn khoá)
        PC gửi CUP_OK                  -> ESP MỞ KHOÁ nút/mic
        bấm nút 3                      -> ESP gửi PRESET_SELECTED + bơm 200 ml
        nhấc cốc                       -> ESP ngắt bơm, PC ĐÓNG CAMERA
    Trả về 0 nếu mọi bước đạt (dùng được trong CI), 1 nếu có bước không đạt.
    """
    from cupfiller.session import AutoFillSession

    # Demo: rút ngắn thời gian "rảnh thì đóng camera" để kịch bản chạy nhanh
    # (mặc định trong máy thật là esp.camera_idle_close_s = 20 giây).
    cfg.set("esp.camera_idle_close_s", 3.0)
    sess = AutoFillSession(cfg, port=args.port or "sim", detector=det, scene=scene,
                           on_state=lambda name, fields: None)
    sim = getattr(sess.bridge, "simulator", None)
    if sim is None:
        print("Chế độ --demo chỉ chạy với --port sim (ESP32 giả lập).")
        return 2

    dt = 1.0 / max(1.0, float(cfg.get("control.loop.fps", 15)))
    results = []

    def good(text):
        results.append((True, text))
        print("  ✔ %s" % text, flush=True)

    def bad(text, extra=""):
        results.append((False, text + ("  " + extra if extra else "")))
        print("  ✘ %s  %s" % (text, extra), flush=True)

    def step(text):
        print("  ... %s" % text, flush=True)

    def spin(seconds):
        t_end = time.time() + seconds
        while time.time() < t_end:
            sess.tick(dt)
            time.sleep(dt)

    def wait(text, cond, timeout_s):
        """Chạy phiên cho tới khi ``cond(tel, sim)`` đúng."""
        t_end = time.time() + timeout_s
        tel = {}
        while time.time() < t_end:
            sess.tick(dt)
            tel = sess.telemetry()
            if cond(tel, sim):
                good(text)
                return tel, True
            time.sleep(dt)
        bad(text, "(hết thời gian %.0f s)" % timeout_s)
        return tel, False

    def n_fill_started():
        return sum(1 for n in sim.event_names() if n == "fill_started")

    print("KỊCH BẢN TỰ CHẠY — ESP32 giả lập + camera giả lập (%s)" % det_name)
    print("Nguyên tắc: máy tính CHỈ bật camera khi ESP báo có cốc;")
    print("           nút bấm CHỈ có tác dụng sau khi máy tính xác nhận cốc (CUP_OK).")

    # ---- 0. nút còn khoá khi chưa có cốc -----------------------------------
    print("\n[0] Chưa có cốc: bấm nút phải KHÔNG có tác dụng")
    step("nhấn nút 1 khi khay trống")
    sim.press_button(0, 120)
    spin(0.5)
    if n_fill_started() == 0 and not (sess.telemetry()["esp"] or {}).get("pumping"):
        good("bấm nút khi chưa có cốc -> ESP bỏ qua (nút còn khoá)")
    else:
        bad("bấm nút khi chưa có cốc -> ESP bỏ qua (nút còn khoá)",
            "nhưng ESP đã bắt đầu bơm!")

    # ---- 1. đặt cốc -> ESP báo -> PC bật camera ----------------------------
    print("\n[1] Đặt cốc: ESP báo CUP_PLACED, máy tính mới BẬT CAMERA")
    if sess.telemetry()["camera_open"]:
        bad("camera phải ĐANG TẮT trước khi đặt cốc")
    else:
        good("camera đang tắt (chỉ bật khi có cốc)")
    step("đặt cốc cao 95 mm lên cảm biến")
    sim.place_cup(95.0)
    wait("ESP gửi CUP_PLACED -> PC BẬT CAMERA",
         lambda t, s: t["camera_open"] and s.cup_present, 5.0)

    # ---- 2. bấm nút trước khi PC xác nhận -> vẫn bị khoá -------------------
    print("\n[2] Camera vừa bật, CHƯA xác nhận xong: bấm nút vẫn bị bỏ qua")
    step("nhấn nút 2 ngay khi cốc chưa được xác nhận")
    locked_before = n_fill_started()
    sim.press_button(1, 120)
    spin(0.3)
    if n_fill_started() == locked_before:
        good("bấm nút trước khi PC xác nhận -> ESP bỏ qua")
    else:
        bad("bấm nút trước khi PC xác nhận -> ESP bỏ qua", "nhưng ESP đã bơm!")

    # ---- 3. PC nhận diện xong -> CUP_OK -> ESP mở khoá ---------------------
    print("\n[3] Máy tính nhận diện cốc -> gửi CUP_OK -> ESP MỞ KHOÁ nút/mic")
    tel, ok = wait("PC gửi CUP_OK và ESP mở khoá (camera đã nhận diện xong)",
                   lambda t, s: s.unlocked and t["state"] in ("READY", "DONE"), 10.0)
    if ok:
        print("      cốc: %s" % (tel.get("cup_meta") or {}))
        good("camera chỉ mở sau khi ESP báo có cốc, đúng luồng thiết kế")

    # ---- 4. bấm nút 3 -> rót 200 ml ----------------------------------------
    print("\n[4] Bấm nút 3 (200 ml) khi đã mở khoá -> relay đóng, bơm nước")
    step("nhấn nút 3")
    sim.press_button(2, 120)
    wait("ESP gửi PRESET_SELECTED mức 200 ml và BẮT ĐẦU BƠM (relay ON)",
         lambda t, s: s.target_ml == 200.0 and (t["esp"] or {}).get("pumping"), 6.0)
    tel, ok = wait("Bơm đủ 200 ml -> ESP gửi FILL_DONE (ESP về trạng thái DONE)",
                   lambda t, s: (t["esp"] or {}).get("state_name") == "DONE", 20.0)
    poured = float(sim.poured_ml)
    print("      đã rót: %.1f ml / mục tiêu %.0f ml" % (poured, 200.0))
    if abs(poured - 200.0) <= 6.0:
        good("lượng nước rót đúng mục tiêu 200 ml")
    else:
        bad("lượng nước rót đúng mục tiêu 200 ml", "(thực tế %.1f ml)" % poured)

    # ---- 5. nhấc cốc -> PC đóng camera ------------------------------------
    print("\n[5] Nhấc cốc: ESP tự ngắt bơm, PC ĐÓNG CAMERA")
    step("nhấc cốc ra khỏi cảm biến")
    sim.remove_cup()
    wait("PC đóng camera, chờ cốc mới (ESP báo CUP_REMOVED)",
         lambda t, s: (not t["camera_open"]) and not s.cup_present, 8.0)

    n_ok = sum(1 for ok_, _ in results if ok_)
    print("\nKẾT QUẢ: %d/%d bước đạt" % (n_ok, len(results)))
    for ok_, text in results:
        print("  %s %s" % ("✔" if ok_ else "✘", text))
    sess.close()
    return 0 if n_ok == len(results) else 1


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
    ap.add_argument("--demo", action="store_true",
                    help="chạy kịch bản tự kiểm chứng rồi thoát (không cần màn hình)")
    ap.add_argument("--no-window", action="store_true",
                    help="không mở cửa sổ (dùng khi chạy qua SSH / Pi không màn hình)")
    args = ap.parse_args()

    # Không có DISPLAY (SSH, Pi chạy nền, Docker) -> tự tắt cửa sổ để khỏi lỗi OpenCV
    window = not args.no_window and not args.demo
    if window and os.name != "nt" and not (os.environ.get("DISPLAY")
                                           or os.environ.get("WAYLAND_DISPLAY")):
        window = False
        print("Không thấy màn hình (DISPLAY trống) -> chạy ở chế độ không cửa sổ.")

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

    if args.demo:
        args.port = args.port or "sim"
        cam.release()                      # để phiên tự mở lại khi ESP báo có cốc
        return run_demo(cfg, args, det, det_name, scene)

    if args.port and not args.classic:
        # chế độ ESP32: camera mở/đóng theo cảm biến cốc của ESP32
        if args.port == "sim":
            cfg.set("control.pump.type", "sim")
        print("Da mo camera se do ESP32 dieu khien (%s)..." % args.port)
        run_auto(cfg, args, det, det_name, scene, window=window)
        cam.release()
        return

    if not window:
        print("Chế độ cũ cần cửa sổ để xem ảnh. Hãy dùng:  "
              "python3 tools/run_pc.py --port sim --synthetic --demo")
        cam.release()
        return 1

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
