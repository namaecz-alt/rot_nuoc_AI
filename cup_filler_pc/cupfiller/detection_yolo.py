"""YOLO định vị hộp cốc + OpenCV đo vạch nước (cần chiếu sáng ngược/backlight).

Model COCO dùng ngay: nhận diện lớp ``cup``. Sau này train model của bạn từ ảnh thật;
YOLO chỉ cần học vị trí cái cốc, còn mực nước đo bằng gradient + cung ellipse meniscus.
"""
from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np

from .detection import CupDetection, CupDetector, cone_volume_ml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CUSTOM = os.path.join(ROOT, "weights", "cup_yolo.pt")
DEFAULT_COCO = os.path.join(ROOT, "weights", "yolo11n_coco.pt")
COCO_CUP_ID = 41


def available_weights(prefer: Optional[str] = None) -> str:
    if prefer and os.path.exists(prefer):
        return prefer
    if os.path.exists(DEFAULT_CUSTOM):
        return DEFAULT_CUSTOM
    if os.path.exists(DEFAULT_COCO):
        return DEFAULT_COCO
    return "yolo11n.pt"  # tự tải weights COCO nếu chưa đóng gói sẵn


class YoloCupDetector:
    def __init__(self, cfg=None, weights: Optional[str] = None, conf: float = 0.30):
        from ultralytics import YOLO

        self.wpath = available_weights(weights)
        self.model = YOLO(self.wpath)
        self.names = self.model.names
        names = list(self.names.values()) if isinstance(self.names, dict) else list(self.names)
        norm = [str(n).lower() for n in names]
        self.custom = "cup" in norm and "water" not in norm and len(norm) == 1
        # model một lớp cup: id 0; model COCO: id 41. Với model nhiều lớp tùy chỉnh,
        # class 0 luôn là cup theo quy ước của tools/train_yolo.py.
        self.cup_class_id = 0 if self.custom or ("cup" in norm and len(norm) <= 2) else COCO_CUP_ID
        self.conf = conf
        self.classical = CupDetector(cfg)
        self.last_boxes = []

    def _cup_box(self, frame: np.ndarray):
        result = self.model.predict(frame, conf=self.conf, verbose=False, imgsz=480)[0]
        best, best_score, boxes = None, -1.0, []
        for box in result.boxes:
            cls = int(box.cls[0])
            if cls != self.cup_class_id:
                continue
            score = float(box.conf[0])
            x0, y0, x1, y1 = [float(v) for v in box.xyxy[0]]
            boxes.append((x0, y0, x1, y1, score))
            area = (x1 - x0) * (y1 - y0)
            if score > best_score and area > 600:
                best, best_score = (x0, y0, x1, y1), score
        self.last_boxes = boxes
        return best

    def detect(self, bgr: np.ndarray, prev: Optional[CupDetection] = None,
               debug: bool = False) -> CupDetection:
        gray = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        res = CupDetection()
        box = self._cup_box(bgr)
        if box is None:
            res.debug = {"gray": gray, "yolo": self.wpath}
            return res
        x0, y0, x1, y1 = box
        res.found = True
        res.x0, res.y0 = int(x0), int(y0)
        res.x1, res.y1 = int(x1), int(y1)
        res.rim_y_px = y0 + 1.0
        res.mm_per_px = self.classical.mm_per_px
        res.base_y_px = y1 + 1.0 - self.classical.base_mm / max(res.mm_per_px, 1e-6)
        res.cup_height_mm = max(0.0, (res.base_y_px - res.rim_y_px) * res.mm_per_px)

        # Fit thành cốc trong hộp YOLO, nếu có dải backlight tối.
        h, w = gray.shape
        cx0, cx1 = max(0, int(x0)), min(w, int(x1) + 1)
        cy0, cy1 = max(0, int(y0)), min(h, int(y1) + 1)
        band = gray[cy0:cy1, cx0:cx1]
        if band.size:
            threshold = 0.80 * float(np.percentile(band, 85))
            mask = (band < threshold).astype(np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            nr, nc = mask.shape
            left = np.full(nr, -1, np.int32)
            right = np.full(nr, -1, np.int32)
            any_m = mask.any(axis=1)
            left[any_m] = np.argmax(mask[any_m], axis=1)
            right[any_m] = nc - 1 - np.argmax(mask[any_m][:, ::-1], axis=1)
            widths = (right - left + 1) * any_m
            valid = widths[widths > 0]
            w_med = float(np.median(valid)) if valid.size else 0.0
            if w_med > 8:
                rows = np.arange(nr)
                good = (left >= 0) & (right > left) & (widths > 0.60 * w_med)
                if good.sum() >= 8:
                    yy = rows[good].astype(np.float64) + cy0
                    wall_px = self.classical.wall_mm / max(res.mm_per_px, 1e-6)
                    al, bl = np.polyfit(yy, left[good].astype(np.float64) + cx0, 1)
                    ar, br = np.polyfit(yy, right[good].astype(np.float64) + cx0, 1)
                    res.left_wall = (float(al), float(bl) + wall_px)
                    res.right_wall = (float(ar), float(br) - wall_px)
                else:
                    res.left_wall = (0.0, x0 + 2)
                    res.right_wall = (0.0, x1 - 2)
            else:
                res.left_wall = (0.0, x0 + 2)
                res.right_wall = (0.0, x1 - 2)
        else:
            res.left_wall = (0.0, x0 + 2)
            res.right_wall = (0.0, x1 - 2)

        res.r_rim_mm = max(1.0, 0.5 * res.width_at(res.rim_y_px + 2) * res.mm_per_px)
        res.r_base_mm = max(1.0, 0.5 * res.width_at(res.base_y_px - 2) * res.mm_per_px)
        res.confidence = 1.0

        # Vạch nước cổ điển chỉ có ý nghĩa khi nền backlight tạo tương phản.
        wl = self.classical._find_waterline(gray, res, prev, debug)
        if wl is not None:
            res.waterline_found = True
            res.waterline_y_px = wl
            h_mm = (res.base_y_px - wl) * res.mm_per_px
            res.water_height_mm = max(0.0, h_mm - self.classical.meniscus_off)
            res.fill_ratio = float(np.clip(res.water_height_mm / max(res.cup_height_mm, 1e-6), 0, 1))
            t = res.fill_ratio
            r_top = res.r_base_mm + (res.r_rim_mm - res.r_base_mm) * t
            res.volume_ml = cone_volume_ml(res.water_height_mm, res.r_base_mm, r_top)
        if debug:
            res.debug.update(gray=gray, yolo=self.wpath, boxes=self.last_boxes)
        return res
