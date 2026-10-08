#!/usr/bin/env python3
"""Tạo NHÃN NHÁP cho ảnh của bạn bằng model COCO đã cài sẵn.

1) Chép ảnh cốc thật vào datasets/cups/user/images/ (có thể bấm 't' trong run_pc.py).
2) Chạy: python tools/autolabel.py
3) Kiểm tra/sửa từng hộp bằng labelImg hoặc CVAT; lưu nhãn YOLO cùng tên .txt
   vào datasets/cups/user/labels/. Mỗi dòng: 0 x_center y_center width height
   (tọa độ chuẩn hóa 0..1, class 0=cup).
4) Chạy python tools/train_yolo.py --epochs 30

COCO YOLO chỉ là gợi ý ban đầu: cốc trong suốt có thể không được phát hiện. Nếu
không thấy hộp, vẽ hộp thủ công trong labelImg. Chỉ cần gán hộp cốc, không gán nước.
"""
from __future__ import annotations

import argparse
import os

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = os.path.join(ROOT, "datasets", "cups")
DEFAULT_COCO = os.path.join(ROOT, "weights", "yolo11n_coco.pt")
COCO_CUP_ID = 41


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=os.path.join(DATASET_DIR, "user", "images"))
    ap.add_argument("--labels", default=os.path.join(DATASET_DIR, "user", "labels"))
    ap.add_argument("--weights", default=DEFAULT_COCO)
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()

    if not os.path.isdir(args.images):
        os.makedirs(args.images, exist_ok=True)
        print("Đã tạo thư mục ảnh:", args.images)
        print("Chép ảnh cốc vào đó rồi chạy lại.")
        return
    os.makedirs(args.labels, exist_ok=True)
    with open(os.path.join(os.path.dirname(args.images), "classes.txt"), "w", encoding="utf-8") as fh:
        fh.write("cup\n")

    from ultralytics import YOLO

    weight = args.weights if os.path.exists(args.weights) else "yolo11n.pt"
    model = YOLO(weight)
    names = model.names
    name_list = list(names.values()) if isinstance(names, dict) else list(names)
    names_lower = [str(v).lower() for v in name_list]
    is_our_model = names_lower == ["cup"]
    cup_id = 0 if is_our_model else COCO_CUP_ID
    print("Dùng weights:", weight, "| class cup id:", cup_id)

    files = sorted(f for f in os.listdir(args.images)
                   if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp"))
    n_lab = 0
    for fn in files:
        path = os.path.join(args.images, fn)
        img = cv2.imread(path)
        if img is None:
            continue
        h, w = img.shape[:2]
        res = model.predict(img, conf=args.conf, verbose=False)[0]
        lines = []
        for box in res.boxes:
            if int(box.cls[0]) != cup_id:
                continue
            x0, y0, x1, y1 = [float(v) for v in box.xyxy[0]]
            cx, cy = (x0 + x1) / (2 * w), (y0 + y1) / (2 * h)
            bw, bh = (x1 - x0) / w, (y1 - y0) / h
            lines.append("0 %.6f %.6f %.6f %.6f" % (cx, cy, bw, bh))
        out = os.path.join(args.labels, os.path.splitext(fn)[0] + ".txt")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        n_lab += 1
        print("  %-28s %d hộp cốc" % (fn, len(lines)))
    print("Đã ghi nhãn nháp %d ảnh tại %s" % (n_lab, args.labels))
    print("Sửa nhãn sau bằng labelImg/CVAT. Ảnh không có hộp vẫn cần thêm nhãn thủ công.")


if __name__ == "__main__":
    main()
