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

Không có thư viện ultralytics thì chương trình tự dùng bộ nhận diện OpenCV cổ điển
(cốc trong suốt + đèn nền) để gợi ý hộp - hoặc gọi thẳng --classical.
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                  # để import được gói cupfiller
DATASET_DIR = os.path.join(ROOT, "datasets", "cups")
DEFAULT_COCO = os.path.join(ROOT, "weights", "yolo11n_coco.pt")
COCO_CUP_ID = 41


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=os.path.join(DATASET_DIR, "user", "images"))
    ap.add_argument("--labels", default=os.path.join(DATASET_DIR, "user", "labels"))
    ap.add_argument("--weights", default=DEFAULT_COCO)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--config", default=None, help="file settings.yaml cho bộ cổ điển")
    ap.add_argument("--classical", action="store_true",
                    help="dùng bộ nhận diện OpenCV cổ điển thay cho YOLO")
    args = ap.parse_args()

    if not os.path.isdir(args.images):
        os.makedirs(args.images, exist_ok=True)
        print("Đã tạo thư mục ảnh:", args.images)
        print("Chép ảnh cốc vào đó rồi chạy lại.")
        return
    os.makedirs(args.labels, exist_ok=True)
    with open(os.path.join(os.path.dirname(args.images), "classes.txt"), "w", encoding="utf-8") as fh:
        fh.write("cup\n")

    def boxes_yolo(img):
        from ultralytics import YOLO

        weight = args.weights if os.path.exists(args.weights) else "yolo11n.pt"
        model = YOLO(weight)
        names = model.names
        name_list = list(names.values()) if isinstance(names, dict) else list(names)
        names_lower = [str(v).lower() for v in name_list]
        cup_id = 0 if names_lower == ["cup"] else COCO_CUP_ID
        res = model.predict(img, conf=args.conf, verbose=False)[0]
        out = []
        for b in res.boxes:
            if int(b.cls[0]) != cup_id:
                continue
            out.append(tuple(float(v) for v in b.xyxy[0]))
        return out, os.path.basename(weight)

    def boxes_classical(img):
        from cupfiller.config import load_config
        from cupfiller.detection import CupDetector

        det = CupDetector(load_config(args.config))
        d = det.detect(img)
        if not d.found:
            return [], "OpenCV cổ điển (không thấy cốc)"
        return [(float(d.x0), float(d.y0), float(d.x1), float(d.y1))], "OpenCV cổ điển (đèn nền)"

    if args.classical:
        boxes_fn = boxes_classical
    else:
        try:
            import ultralytics  # noqa: F401

            boxes_fn = boxes_yolo
        except ImportError:
            print("Không có thư viện ultralytics -> dùng bộ nhận diện OpenCV cổ điển.")
            print("(muốn dùng YOLO COCO: pip install ultralytics)")
            boxes_fn = boxes_classical
    print("Bộ gợi ý nhãn:", "OpenCV cổ điển" if boxes_fn is boxes_classical else "YOLO")

    files = sorted(f for f in os.listdir(args.images)
                   if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp"))
    n_lab = 0
    for fn in files:
        path = os.path.join(args.images, fn)
        img = cv2.imread(path)
        if img is None:
            continue
        h, w = img.shape[:2]
        boxes, _src = boxes_fn(img)
        lines = []
        for x0, y0, x1, y1 in boxes:
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
