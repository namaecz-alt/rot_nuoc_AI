#!/usr/bin/env python3
"""Chạy thử MODEL MỰC NƯỚC (``weights/muc_nuoc_yolo.pt``) trên ảnh / webcam.

Model có 4 lớp, mỗi lớp là một dải mực nước theo **chiều cao cốc**:
    "0-"  = dưới 30%   ·  "30-" = 30-60%  ·  "60-" = 60-90%  ·  "90-" = trên 90%

Ví dụ
-----
    # xem model có dùng được không (đã cài ultralytics chưa, file weights có chưa)
    python3 tools/level_check.py --check

    # đọc thử một tấm ảnh (có thể kèm thông số cốc để quy ra ml)
    python3 tools/level_check.py --image anh_coc.jpg --height-mm 100 --base-mm 25 --rim-mm 35

    # quét webcam trong 15 giây, vẽ hộp lên màn hình
    python3 tools/level_check.py --camera 0 --seconds 15 --save ket_qua.png

Cài thư viện (một lần):  pip install ultralytics
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cupfiller.level_yolo import LEVEL_BANDS, WaterLevelDetector, ml_at_frac  # noqa: E402


class _CupGeom:
    """Hình học cốc tối thiểu để đổi dải mực nước -> ml (giống CupDetection)."""

    def __init__(self, height_mm: float, base_mm: float, rim_mm: float, capacity_ml: float = 0.0):
        self.cup_height_mm = float(height_mm)
        self.r_base_mm = float(base_mm)
        self.r_rim_mm = float(rim_mm)
        self.capacity_ml = float(capacity_ml)

    @property
    def volume_ml(self) -> float:
        return self.capacity_ml or ml_at_frac(1.0, self)


def _draw(frame, reading, cup=None):
    import cv2

    if reading is not None and reading.box:
        x0, y0, x1, y1 = reading.box
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 200, 255), 2)
        cv2.putText(frame, reading.label, (x0, max(20, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (0, 200, 255), 2, cv2.LINE_AA)
    txt = ("khong thay muc nuoc" if reading is None else
           "muc %s (%.0f-%.0f%% chieu cao coc) conf %.2f%s" % (
               reading.label, reading.lo * 100, reading.hi * 100, reading.conf,
               "" if cup is None else "  ~%d-%d ml" % (ml_at_frac(reading.lo, cup),
                                                       ml_at_frac(reading.hi, cup))))
    cv2.putText(frame, txt, (12, frame.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chạy thử model mực nước (4 lớp 0-/30-/60-/90-)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--image", help="đường dẫn ảnh cần đọc")
    src.add_argument("--camera", type=int, help="số hiệu webcam (0 = camera mặc định)")
    src.add_argument("--check", action="store_true", help="chỉ kiểm tra model có dùng được không")
    ap.add_argument("--weights", default=None, help="đường dẫn model (mặc định weights/muc_nuoc_yolo.pt)")
    ap.add_argument("--conf", type=float, default=0.35, help="ngưỡng tin cậy (mặc định 0.35)")
    ap.add_argument("--height-mm", type=float, default=0.0, help="chiều cao trong lòng cốc (mm)")
    ap.add_argument("--base-mm", type=float, default=0.0, help="bán kính trong đáy cốc (mm)")
    ap.add_argument("--rim-mm", type=float, default=0.0, help="bán kính trong miệng cốc (mm)")
    ap.add_argument("--seconds", type=float, default=10.0, help="quét webcam bao lâu (mặc định 10 s)")
    ap.add_argument("--save", default=None, help="lưu ảnh đã vẽ hộp (chỉ dùng với --image/--camera)")
    ap.add_argument("--json", action="store_true", help="in kết quả dạng JSON")
    args = ap.parse_args(argv)

    det = WaterLevelDetector(weights=args.weights, conf=args.conf)
    cup = None
    if args.height_mm > 0 and args.base_mm > 0 and args.rim_mm > 0:
        cup = _CupGeom(args.height_mm, args.base_mm, args.rim_mm)
        cup.capacity_ml = ml_at_frac(1.0, cup)

    if not det.available:
        print("Model CHƯA dùng được.")
        print("  * weights : %s%s" % (det.weights, "" if os.path.exists(det.weights) else "  (không thấy file)"))
        print("  * lỗi     : %s" % (det.last_error or "chưa cài ultralytics"))
        print("\nCài thư viện rồi chạy lại:  pip install ultralytics")
        print("Nhãn các lớp phải là: %s" % ", ".join(LEVEL_BANDS))
        return 2 if args.check else 1

    if args.check:
        names = det.names() or list(LEVEL_BANDS)
        print("Model OK: %s" % det.weights)
        print("Các lớp: %s" % ", ".join(str(n) for n in names))
        if cup is not None:
            print("Cốc %.0f mm (đáy %.0f mm, miệng %.0f mm) = %.0f ml" %
                  (cup.cup_height_mm, cup.r_base_mm, cup.r_rim_mm, cup.capacity_ml))
            for lab, (lo, hi) in LEVEL_BANDS.items():
                print("   %-4s %2.0f-%2.0f%% chiều cao  ~%5.0f-%5.0f ml" %
                      (lab, lo * 100, hi * 100, ml_at_frac(lo, cup), ml_at_frac(hi, cup)))
        return 0

    import cv2

    cap = None
    single = None
    if args.image:
        single = cv2.imread(args.image)
        if single is None:
            print("Không đọc được ảnh: %s" % args.image)
            return 1
    else:
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            print("Không mở được webcam %d" % args.camera)
            return 1

    t_end = time.time() + args.seconds
    n = 0
    last = None
    try:
        while True:
            ok, frame = (True, single) if single is not None else cap.read()
            if not ok or frame is None:
                break
            n += 1
            r = det.detect(frame)
            if r is not None:
                last = r
                if args.json:
                    print(json.dumps(r.as_dict(), ensure_ascii=False))
                else:
                    extra = ""
                    if cup is not None:
                        extra = "  ->  ~%.0f-%.0f ml" % (ml_at_frac(r.lo, cup),
                                                         ml_at_frac(r.hi, cup))
                    print("khung %4d: mức %-4s %2.0f-%2.0f%% chiều cao cốc  conf %.2f%s"
                          % (n, r.label, r.lo * 100, r.hi * 100, r.conf, extra))
                if args.save:
                    cv2.imwrite(args.save, _draw(frame.copy(), r, cup))
            else:
                if single is not None:
                    print("không thấy mực nước trong ảnh %s" % args.image)
                elif args.json:
                    print(json.dumps({"label": None}))
            if single is not None or time.time() > t_end:
                break
    finally:
        if cap is not None:
            cap.release()

    if n == 0:
        print("Không đọc được khung hình nào.")
        return 1
    if last is None:
        print("Đã xem %d khung nhưng KHÔNG thấy mực nước (model cần ảnh cốc rõ, đủ sáng)." % n)
        return 3
    if cup is not None and not args.json:
        print("Ước lượng thể tích: ~%.0f-%.0f ml (cốc %.0f ml)" %
              (ml_at_frac(last.lo, cup), ml_at_frac(last.hi, cup), cup.capacity_ml))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
