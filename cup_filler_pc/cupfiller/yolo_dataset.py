"""Dataset YOLO một lớp (cup) cho cốc trong suốt, có bộ tổng hợp + nhãn tự động.

YOLO chỉ cần định vị CỐC. Đo vạch nước dùng pipeline OpenCV riêng (cần backlight),
để người dùng không phải gán nhãn vạch nước bằng tay.

Cấu trúc:
  datasets/cups/images/{train,val}/
  datasets/cups/labels/{train,val}/
  datasets/cups/user/images/   # ảnh cốc thật của bạn
  datasets/cups/user/labels/   # nhãn YOLO (mỗi ảnh .txt, class 0=cup)
"""
from __future__ import annotations

import os

import cv2
import numpy as np

from .synthetic import CupScene, make_test_set

DATASET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datasets", "cups")
NAMES = ["cup"]


def _yolo_line(x0: float, y0: float, x1: float, y1: float, w: int, h: int) -> str:
    cx = (x0 + x1) / 2.0 / w
    cy = (y0 + y1) / 2.0 / h
    bw = (x1 - x0) / w
    bh = (y1 - y0) / h
    return "0 %.5f %.5f %.5f %.5f" % (cx, cy, bw, bh)


def _cup_label(scene: CupScene, w: int, h: int) -> str:
    cup = scene.cup
    x_l, _ = scene.to_px(scene.cup_dx_mm - (cup.r_rim_mm + cup.wall_mm), 0)
    x_r, _ = scene.to_px(scene.cup_dx_mm + (cup.r_rim_mm + cup.wall_mm), 0)
    _, y_t = scene.to_px(0, cup.y_rim_mm)
    _, y_b = scene.to_px(0, cup.y_base_mm - cup.base_mm)
    x0, x1 = max(0, min(x_l, x_r)), min(w, max(x_l, x_r))
    y0, y1 = max(0, min(y_t, y_b)), min(h, max(y_t, y_b))
    if x1 - x0 <= 8 or y1 - y0 <= 8:
        return ""
    return _yolo_line(x0, y0, x1, y1, w, h)


def _empty_background(scene: CupScene, rng: np.random.Generator) -> np.ndarray:
    """Nền backlight không có cốc (negative image thật, không gắn nhãn sai)."""
    h, w = scene.height, scene.width
    base = np.full((h, w, 3), scene.bg_brightness, np.float32)
    x0, _ = scene.to_px(scene.strip_x_mm[0], 0)
    x1, _ = scene.to_px(scene.strip_x_mm[1], 0)
    cols = np.arange(w, dtype=np.float32)
    edge = 6.0
    strip = np.clip((cols - (x0 - edge)) / edge, 0, 1) * np.clip(((x1 + edge) - cols) / edge, 0, 1)
    grain = 1.0 + 0.025 * np.sin(cols * 0.35) + 0.015 * rng.standard_normal(w)
    band = (scene.strip_brightness * strip * grain).astype(np.float32)
    base += band[None, :, None]
    base *= np.asarray([1.0, 1.01, 0.99], np.float32)[None, None, :]
    if scene.noise_sigma > 0:
        base += rng.normal(0, scene.noise_sigma, base.shape).astype(np.float32)
    return np.clip(base, 0, 255).astype(np.uint8)


def write_data_yaml(root: str = DATASET_DIR) -> str:
    """Viết data.yaml với đường dẫn tuyệt đối tính lại trên máy hiện tại."""
    os.makedirs(root, exist_ok=True)
    yaml_path = os.path.join(root, "data.yaml")
    with open(yaml_path, "w", encoding="utf-8") as fh:
        fh.write(
            "path: %s\ntrain: images/train\nval: images/val\nnames:\n  0: cup\n"
            % os.path.abspath(root).replace("\\", "/")
        )
    return yaml_path


def generate_dataset(root: str = DATASET_DIR, n_train: int = 240, n_val: int = 60,
                     seed: int = 2024, empty_ratio: float = 0.08) -> str:
    """Sinh tập train/val ảnh cốc giả lập + nhãn chính xác tự động."""
    rng = np.random.default_rng(seed)
    for split, n in (("train", n_train), ("val", n_val)):
        img_dir = os.path.join(root, "images", split)
        lab_dir = os.path.join(root, "labels", split)
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lab_dir, exist_ok=True)
        data = make_test_set(n, seed=seed + (0 if split == "train" else 999))
        for i, (scene, img, _gt) in enumerate(data):
            name = "%s_%04d" % (split, i)
            is_negative = rng.random() < empty_ratio
            if is_negative:
                img = _empty_background(scene, rng)
                lbl = ""
            else:
                lbl = _cup_label(scene, img.shape[1], img.shape[0])
            cv2.imwrite(os.path.join(img_dir, name + ".png"), img)
            with open(os.path.join(lab_dir, name + ".txt"), "w", encoding="utf-8") as fh:
                fh.write(lbl)
    return write_data_yaml(root)


def merge_user_images(root: str = DATASET_DIR) -> int:
    """Copy ảnh và nhãn YOLO do người dùng gán vào phần train; class duy nhất là cup."""
    import shutil

    src_i = os.path.join(root, "user", "images")
    src_l = os.path.join(root, "user", "labels")
    if not os.path.isdir(src_i):
        return 0
    dst_i = os.path.join(root, "images", "train")
    dst_l = os.path.join(root, "labels", "train")
    os.makedirs(dst_i, exist_ok=True)
    os.makedirs(dst_l, exist_ok=True)
    n = 0
    for fn in sorted(os.listdir(src_i)):
        base, ext = os.path.splitext(fn)
        if ext.lower() not in (".png", ".jpg", ".jpeg", ".bmp"):
            continue
        lbl = os.path.join(src_l, base + ".txt")
        if not os.path.exists(lbl):
            print("  [warn] bo qua %s: chua co nhan YOLO (class 0=cup)" % fn)
            continue
        shutil.copy2(os.path.join(src_i, fn), os.path.join(dst_i, "user_" + fn))
        shutil.copy2(lbl, os.path.join(dst_l, "user_" + base + ".txt"))
        n += 1
    return n
