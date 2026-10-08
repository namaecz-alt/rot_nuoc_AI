#!/usr/bin/env python3
"""Công cụ gán nhãn ảnh cốc YOLO nhỏ gọn, không cần labelImg.

Dùng chuột kéo một hộp quanh TOÀN BỘ cốc rồi nhấn ENTER/SPACE để lưu.
Sau khi kéo hộp, nhấn C hoặc ESC để huỷ hộp; khi quay lại ảnh:
  n = ảnh không có cốc (lưu nhãn rỗng) | s = bỏ qua | q = thoát.

Ví dụ:
  python tools/label_cups.py
  python tools/label_cups.py --images datasets/cups/user/images

Nhãn đầu ra: datasets/cups/user/labels/<tên ảnh>.txt, class 0=cup.
"""
from __future__ import annotations

import argparse
import os

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = os.path.join(ROOT, "datasets", "cups")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=os.path.join(DATASET_DIR, "user", "images"))
    ap.add_argument("--labels", default=os.path.join(DATASET_DIR, "user", "labels"))
    ap.add_argument("--force", action="store_true", help="ghi đè nhãn đang có để sửa nhãn tự động")
    args = ap.parse_args()
    if not os.path.isdir(args.images):
        raise SystemExit("Không tìm thấy thư mục ảnh: " + args.images)
    os.makedirs(args.labels, exist_ok=True)
    files = sorted(f for f in os.listdir(args.images)
                   if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp"))
    if not files:
        raise SystemExit("Thư mục chưa có ảnh.")
    with open(os.path.join(os.path.dirname(args.images), "classes.txt"), "w", encoding="utf-8") as f:
        f.write("cup\n")
    win = "Gán nhãn cốc — kéo hộp và Enter | n=không cốc | s=bỏ qua | q=thoát"
    for i, fn in enumerate(files, 1):
        img = cv2.imread(os.path.join(args.images, fn))
        if img is None:
            continue
        out = os.path.join(args.labels, os.path.splitext(fn)[0] + ".txt")
        if os.path.exists(out) and not args.force:
            print("[%d/%d] Đã có nhãn, bỏ qua: %s (dùng --force để sửa)" % (i, len(files), fn))
            continue
        print("[%d/%d] %s — kéo hộp quanh cốc rồi Enter, hoặc để trống nếu không có cốc"
              % (i, len(files), fn))
        cv2.imshow(win, img)
        cv2.waitKey(1)
        x, y, w, h = cv2.selectROI(win, img, fromCenter=False, showCrosshair=True)
        if w > 0 and h > 0:
            ih, iw = img.shape[:2]
            cx, cy = (x + w / 2) / iw, (y + h / 2) / ih
            bw, bh = w / iw, h / ih
            with open(out, "w", encoding="utf-8") as f:
                f.write("0 %.6f %.6f %.6f %.6f\n" % (cx, cy, bw, bh))
            print("  Đã lưu hộp cốc ->", out)
            continue
        cv2.imshow(win, img)
        key = cv2.waitKey(0) & 0xFF
        if key == ord("q"):
            break
        if key == ord("n"):
            open(out, "w", encoding="utf-8").close()
            print("  Đã lưu ảnh âm (không cốc).")
        else:
            print("  Bỏ qua ảnh; nhấn Enter để tiếp tục.")
    cv2.destroyAllWindows()
    print("Xong. Kiểm tra labels/ rồi chạy tools/train_yolo.py.")


if __name__ == "__main__":
    main()
