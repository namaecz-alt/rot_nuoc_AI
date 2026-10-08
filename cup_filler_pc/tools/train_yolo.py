#!/usr/bin/env python3
"""Train/fine-tune YOLO để tìm vị trí cốc trên ảnh của bạn.

Tối giản gán nhãn: chỉ vẽ MỘT hộp bao quanh CỐC (class 0=cup).
Đo vạch nước được làm bằng OpenCV riêng nên không cần gán nhãn waterline.

Lần đầu chưa có ảnh thật, chương trình tạo 240 ảnh tổng hợp train + 60 val và nhãn
sẵn để kiểm tra toàn bộ pipeline. Ảnh thật của bạn thêm sau ở:
  datasets/cups/user/images/   (ảnh)
  datasets/cups/user/labels/   (nhãn YOLO, cùng tên .txt; class 0=cup)

Chạy:
  python tools/train_yolo.py --epochs 30
  python tools/train_yolo.py --epochs 50 --device 0   # nếu máy có GPU NVIDIA

Model fine-tuned lưu tại weights/cup_yolo.pt; tools/run_pc.py tự dùng model này.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.yolo_dataset import DATASET_DIR, generate_dataset, merge_user_images, write_data_yaml  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--imgsz", type=int, default=480)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--model", default=os.path.join(ROOT, "weights", "yolo11n_coco.pt"),
                    help="weight COCO nền (mặc định dùng bản local)")
    ap.add_argument("--n-train", type=int, default=240)
    ap.add_argument("--n-val", type=int, default=60)
    ap.add_argument("--device", default="cpu", help="cpu hoặc 0 nếu có GPU NVIDIA")
    ap.add_argument("--dataset", default=DATASET_DIR)
    args = ap.parse_args()

    try:
        import ultralytics  # noqa: F401
    except ImportError:
        raise SystemExit("Thiếu thư viện ultralytics (bắt buộc để train YOLO).\n"
                         "    pip install ultralytics\n"
                         "Muốn dùng ngay không cần train: bộ nhận diện OpenCV cổ điển\n"
                         "(mặc định của tools/run_pc.py; xem thêm tools/detect_pc.py --classical).")

    img_train = os.path.join(args.dataset, "images", "train")
    if not os.path.isdir(img_train) or not any(os.scandir(img_train)):
        print("[1/3] Sinh dữ liệu tổng hợp cốc trong suốt (nhãn hộp cốc tự động)...")
        generate_dataset(args.dataset, n_train=args.n_train, n_val=args.n_val)
    else:
        print("[1/3] Dùng dataset có sẵn.")
    n_user = merge_user_images(args.dataset)
    yaml_path = write_data_yaml(args.dataset)  # đường dẫn tự cập nhật trên máy Windows/macOS/Linux
    n_tr = sum(1 for f in os.listdir(img_train) if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")))
    img_val = os.path.join(args.dataset, "images", "val")
    n_va = sum(1 for f in os.listdir(img_val) if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")))
    print("[2/3] %d ảnh train (đã ghép %d ảnh thật), %d ảnh val." % (n_tr, n_user, n_va))

    from ultralytics import YOLO

    model_path = args.model
    if not os.path.exists(model_path) and os.path.basename(model_path) == "yolo11n_coco.pt":
        model_path = "yolo11n.pt"  # auto-download nếu người dùng bỏ model local
    model = YOLO(model_path)
    print("[3/3] Train YOLO từ %s: %d epochs, imgsz %d, device %s..."
          % (model_path, args.epochs, args.imgsz, args.device))
    model.train(
        data=yaml_path,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=0,
        project=os.path.join(ROOT, "runs"),
        name="cup_yolo",
        exist_ok=True,
        verbose=True,
    )
    best = os.path.join(ROOT, "runs", "cup_yolo", "weights", "best.pt")
    out_dir = os.path.join(ROOT, "weights")
    os.makedirs(out_dir, exist_ok=True)
    shutil.copy2(best, os.path.join(out_dir, "cup_yolo.pt"))
    print("\nĐã lưu weights/cup_yolo.pt — lần tới run_pc.py sẽ dùng model đã train.")
    metrics = model.val(data=yaml_path, device=args.device, workers=0, verbose=False)
    print("Validation trên dataset hiện tại: mAP50-95=%.3f | mAP50=%.3f"
          % (metrics.box.map, metrics.box.map50))
    print("Lưu ý: nếu val chỉ là ảnh tổng hợp, metric KHÔNG đại diện độ chính xác trên ảnh thật.")


if __name__ == "__main__":
    main()
