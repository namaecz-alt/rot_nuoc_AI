#!/usr/bin/env python3
"""Thử NHẬN DIỆN CỐC trên máy tính bằng webcam/ảnh/video. KHÔNG bật bơm.

Chạy từ thư mục dự án:
  python tools/detect_pc.py --camera 0
  python tools/detect_pc.py --image cup.jpg --save output.jpg
  python tools/detect_pc.py --source video.mp4
  python tools/detect_pc.py --synthetic --frames 10 --save out.jpg   # không cần camera
  python tools/detect_pc.py --weights weights/cup_yolo.pt           # model bạn tự train
  python tools/detect_pc.py --camera 0 --classical                   # ép dùng OpenCV cổ điển

Hai bộ nhận diện:
  * YOLO (mặc định khi có thư viện `ultralytics`): tìm hộp cốc, dùng được cả khi nền phức tạp.
    Chưa có weights local -> tự tải yolo11n.pt (cần internet).
  * OpenCV cổ điển: đúng thế mạnh của dự án (cốc trong suốt + đèn nền) - cũng đo luôn VẠCH NƯỚC.
    Tự động dùng khi thiếu ultralytics, hoặc khi bạn gọi --classical.
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                  # để import được gói cupfiller
COCO_LOCAL = os.path.join(ROOT, "weights", "yolo11n_coco.pt")
CUSTOM_LOCAL = os.path.join(ROOT, "weights", "cup_yolo.pt")


def _headless(args) -> bool:
    """Không có màn hình (SSH, Docker, Pi chạy nền) thì đừng gọi cv2.imshow."""
    if args.no_window:
        return True
    if os.name != "nt" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return True
    return False


def _make_annotator(args):
    """Trả về (hàm vẽ khung, tên bộ nhận diện)."""
    if not args.classical:
        try:
            from ultralytics import YOLO
        except ImportError:
            print("Không có thư viện ultralytics -> dùng bộ nhận diện OpenCV cổ điển.")
            print("(muốn dùng YOLO: pip install ultralytics)")
            YOLO = None
        if YOLO is not None:
            weight = args.weights or (CUSTOM_LOCAL if os.path.exists(CUSTOM_LOCAL) else
                                      COCO_LOCAL if os.path.exists(COCO_LOCAL) else "yolo11n.pt")
            model = YOLO(weight)
            names = model.names
            names_list = list(names.values()) if isinstance(names, dict) else list(names)
            names_lower = [str(x).lower() for x in names_list]
            cup_id = 0 if names_lower == ["cup"] else 41

            def annotate_yolo(frame):
                result = model.predict(frame, conf=args.conf, imgsz=640, verbose=False)[0]
                out = frame.copy()
                found = 0
                for b in result.boxes:
                    cls = int(b.cls[0])
                    if cls != cup_id:
                        continue
                    found += 1
                    x0, y0, x1, y1 = [int(v) for v in b.xyxy[0]]
                    conf = float(b.conf[0])
                    cv2.rectangle(out, (x0, y0), (x1, y1), (30, 220, 40), 2)
                    cv2.putText(out, "COC %.2f" % conf, (x0, max(22, y0 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 220, 40), 2, cv2.LINE_AA)
                status = "THAY %d COC" % found if found else "CHUA THAY COC"
                cv2.rectangle(out, (0, 0), (out.shape[1], 40), (30, 30, 30), -1)
                cv2.putText(out, status + " | model: " + os.path.basename(weight), (10, 27),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
                return out

            return annotate_yolo, "YOLO " + os.path.basename(weight)

    # ---- OpenCV cổ điển (cốc trong suốt + đèn nền, đo luôn vạch nước) ----
    from cupfiller.config import load_config
    from cupfiller.detection import CupDetector, draw_overlay

    det = CupDetector(load_config(args.config))

    def annotate_cv(frame):
        d = det.detect(frame)
        text = []
        if d.found:
            text.append("cao %.0f mm | mieng Ø%.0f mm | ~%.0f ml"
                        % (d.cup_height_mm, 2 * d.r_rim_mm, d.volume_ml))
            if d.waterline_found:
                text.append("nuoc %.1f mm (%.0f%%)" % (d.water_height_mm, 100 * d.fill_ratio))
            else:
                text.append("chua thay vach nuoc")
        return draw_overlay(frame.copy(), d, text=text)

    return annotate_cv, "OpenCV cổ điển (đèn nền)"


def main():
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--camera", default=None, help="số webcam, ví dụ 0 hoặc 1")
    source.add_argument("--source", default=None, help="đường dẫn video")
    source.add_argument("--image", default=None, help="đường dẫn ảnh")
    source.add_argument("--synthetic", action="store_true",
                        help="cốc giả lập (thử ngay, không cần camera)")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--config", default=None, help="file settings.yaml (cho bộ cổ điển)")
    ap.add_argument("--classical", action="store_true",
                    help="ép dùng OpenCV cổ điển (mặc định tự chọn)")
    ap.add_argument("--frames", type=int, default=0,
                    help="chỉ chạy N khung rồi thoát (dùng cho --synthetic/CI)")
    ap.add_argument("--save", default=None, help="lưu ảnh kết quả (--image hoặc --synthetic)")
    ap.add_argument("--no-window", action="store_true", help="không mở cửa sổ")
    args = ap.parse_args()

    annotate, det_name = _make_annotator(args)
    window = not _headless(args)
    print("Bộ nhận diện:", det_name)

    if args.image or args.synthetic:
        if args.synthetic:
            from cupfiller.synthetic import SyntheticCupCamera, CupScene

            cam_syn = SyntheticCupCamera(CupScene(), fps=15)
            n = args.frames or 1
            frame, det_last = None, None
            for _ in range(max(1, n)):
                ok, frame = cam_syn.read()
                if not ok:
                    break
                out = annotate(frame)
                det_last = out
            if frame is None:
                raise SystemExit("Không đọc được khung hình giả lập.")
            det_last = out
            frame = out
        else:
            frame = cv2.imread(args.image)
            if frame is None:
                raise SystemExit("Khong doc duoc anh: " + args.image)
            frame = annotate(frame)
        if args.save:
            cv2.imwrite(args.save, frame)
            print("Da luu:", args.save)
        if args.synthetic:                       # in kết quả tóm tắt cho chế độ giả lập
            print("Khung hình giả lập: đã xử lý %d khung -> %s" % (args.frames or 1, args.save or "(không lưu)"))
        if not window:
            print("(không có màn hình -> bỏ qua cửa sổ hiển thị)")
            return
        cv2.imshow("Nhan dien coc - bam q de thoat", frame)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        return

    if args.source:
        cap = cv2.VideoCapture(args.source)
    else:
        dev = args.camera if args.camera is not None else "0"
        cap = cv2.VideoCapture(int(dev) if str(dev).isdigit() else dev)
    if not cap.isOpened():
        raise SystemExit("Khong mo duoc camera/video. Thu --camera 1 hoac --source video.mp4")
    print("Dang chay. Nhan q de thoat | bộ nhận diện:", det_name)
    if not window:
        print("Chế độ không cửa sổ: đọc %d khung rồi thoát." % (args.frames or 30))
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            if args.source:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            break
        out = annotate(frame)
        n += 1
        if window:
            cv2.imshow("Nhan dien coc - bam q de thoat", out)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        if args.save and n == 1:
            cv2.imwrite(args.save, out)
            print("Da luu khung dau:", args.save)
        if args.frames and n >= args.frames:
            break
    cap.release()
    if window:
        cv2.destroyAllWindows()
    print("Đã xử lý %d khung." % n)


if __name__ == "__main__":
    main()
