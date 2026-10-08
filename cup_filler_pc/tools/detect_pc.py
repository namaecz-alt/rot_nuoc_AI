#!/usr/bin/env python3
"""Thử NHẬN DIỆN CỐC trên máy tính bằng webcam/ảnh/video (YOLO pretrained COCO).

Dùng làm bước đầu; không bật bơm. Chạy từ thư mục dự án:
  python tools/detect_pc.py --camera 0
  python tools/detect_pc.py --image cup.jpg --save output.jpg
  python tools/detect_pc.py --source video.mp4
  python tools/detect_pc.py --weights weights/cup_yolo.pt   # model bạn tự train

Nếu chưa có weights local, chương trình sẽ tải yolo11n.pt từ Ultralytics (internet).
YOLO COCO có lớp cup; cốc trong suốt có thể cần backlight/ảnh thật để fine-tune.
"""
from __future__ import annotations

import argparse
import os

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COCO_LOCAL = os.path.join(ROOT, "weights", "yolo11n_coco.pt")
CUSTOM_LOCAL = os.path.join(ROOT, "weights", "cup_yolo.pt")


def main():
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--camera", default=None, help="số webcam, ví dụ 0 hoặc 1")
    source.add_argument("--source", default=None, help="đường dẫn video")
    source.add_argument("--image", default=None, help="đường dẫn ảnh")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--save", default=None, help="lưu ảnh kết quả (chỉ --image)")
    args = ap.parse_args()

    from ultralytics import YOLO

    weight = args.weights or (CUSTOM_LOCAL if os.path.exists(CUSTOM_LOCAL) else
                              COCO_LOCAL if os.path.exists(COCO_LOCAL) else "yolo11n.pt")
    model = YOLO(weight)
    names = model.names
    names_list = list(names.values()) if isinstance(names, dict) else list(names)
    names_lower = [str(x).lower() for x in names_list]
    cup_id = 0 if names_lower == ["cup"] else 41

    def annotate(frame):
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

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            raise SystemExit("Khong doc duoc anh: " + args.image)
        out = annotate(frame)
        if args.save:
            cv2.imwrite(args.save, out)
            print("Da luu:", args.save)
        cv2.imshow("Nhan dien coc - bam q de thoat", out)
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
    print("Dang chay. Nhan q de thoat | model:", weight)
    while True:
        ok, frame = cap.read()
        if not ok:
            if args.source:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            break
        cv2.imshow("Nhan dien coc - bam q de thoat", annotate(frame))
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
