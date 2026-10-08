#!/usr/bin/env python3
"""Wizard hiệu chuẩn máy rót - chạy trên máy có màn hình hoặc chế độ --auto (giả lập).

Bước 1  SCALE        : bấm 2 điểm trên ảnh cách nhau một khoảng ĐÃ BIẾT (mm)
                       (vd: dán thước giấy sau cốc, bấm 2 vạch cách nhau 50 mm)
Bước 2  STRIP        : xem overlay dải sáng dò được, chấp nhận hoặc nhập tay
Bước 3  FLOW         : bơm vào cốc đong ở 3 mức PWM, nhập thể tích thực đo
                       -> đường cong flow_curve (pwm -> ml/s)
Bước 4  MENISCUS     : rót tay tới vạch known_ml trên cốc đong, máy tự so sánh
                       số đo vạch nước với thể tích thật -> meniscus_offset_mm
Kết quả ghi vào config/calibration.json (được load đè lên settings).

Không phần cứng:  python3 tools/calibrate.py --auto   (tự chạy trên ảnh giả lập)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.config import load_config                     # noqa: E402
from cupfiller.synthetic import SyntheticCupCamera, CupScene, CupGeometry  # noqa: E402
from cupfiller.camera import open_camera                     # noqa: E402
from cupfiller.pump import open_pump                         # noqa: E402
from cupfiller.detection import CupDetector                  # noqa: E402

OUT = os.path.join(ROOT, "config", "calibration.json")


def _click_points(frame, title):
    pts = []
    win = "calib"

    def cb(ev, x, y, f, u):
        if ev == cv2.EVENT_LBUTTONDOWN:
            pts.append((x, y))

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, cb)
    while len(pts) < 2:
        vis = frame.copy()
        for p in pts:
            cv2.circle(vis, p, 4, (0, 0, 255), -1)
        if len(pts) == 1:
            cv2.line(vis, pts[0], pts[0], (0, 255, 0), 1)
        cv2.putText(vis, title, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
        cv2.imshow(win, vis)
        if cv2.waitKey(20) == 27:
            cv2.destroyWindow(win)
            return None
    cv2.destroyWindow(win)
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--auto", action="store_true", help="chạy giả lập, không cần phần cứng")
    ap.add_argument("--known_mm", type=float, default=50.0, help="khoảng cách 2 điểm bấm ở bước 1")
    ap.add_argument("--known_ml", type=float, default=200.0, help="vạch đong ở bước 4")
    args = ap.parse_args()

    cfg = load_config(args.config)
    out = {}
    if args.auto:
        # giả lập: scale thật của scene, offset 0
        scene = SyntheticCupCamera(CupScene(cup=CupGeometry()), fps=15)
        out = {
            "mm_per_px": round(1.0 / scene.scene.scale_px_per_mm, 5),
            "optical_center_px": [320.0, 240.0],
            "meniscus_offset_mm": 0.0,
            "camera_offset_mm": 0.0,
            "flow_curve": {"pwm": [0.35, 0.5, 0.7, 0.85, 1.0], "ml_per_s": [8, 16, 27, 34, 40]},
            "_note": "gia lap --auto",
        }
    else:
        cam = open_camera(cfg)
        pump = open_pump(cfg, getattr(cam, "synthetic", None))
        det = CupDetector(cfg)
        ok, frame = cam.read()
        # ---- bước 1: scale
        pts = _click_points(frame, "Bam 2 diem cach nhau %.0f mm (ESC bo)" % args.known_mm)
        if pts is None:
            print("Huy."); return
        d_px = float(np.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]))
        out["mm_per_px"] = round(args.known_mm / max(d_px, 1e-6), 5)
        out["optical_center_px"] = [frame.shape[1] / 2.0, frame.shape[0] / 2.0]
        print("mm_per_px =", out["mm_per_px"])
        # ---- bước 2: strip
        det.mm_per_px = out["mm_per_px"]
        r = det.detect(frame, debug=True)
        vis = frame.copy()
        if "strip" in r.debug:
            sx0, sx1 = r.debug["strip"]
            cv2.rectangle(vis, (sx0, 0), (sx1, frame.shape[0]), (0, 255, 0), 2)
        cv2.imshow("strip", vis)
        print("Nhan Enter neu vach xanh dung la dai den nen...")
        cv2.waitKey(0)
        cv2.destroyAllWindows()
        # ---- bước 3: flow curve
        pwm_list, flow_list = [], []
        for pwm in (0.5, 0.75, 1.0):
            t = float(input("Bom PWM %.2f trong bao nhieu giay? [5] " % pwm) or 5)
            pump.set_pwm(pwm)
            cv2.waitKey(int(t * 1000))
            pump.off()
            ml = float(input("The tich do duoc (ml)? "))
            pwm_list.append(pwm)
            flow_list.append(round(ml / max(t, 1e-6), 2))
        out["flow_curve"] = {"pwm": pwm_list, "ml_per_s": flow_list}
        # ---- bước 4: meniscus offset
        input("Dat coc dong co vach %.0f ml vao may, nhan Enter..." % args.known_ml)
        ok, frame = cam.read()
        det.mm_per_px = out["mm_per_px"]
        r = det.detect(frame)
        if r.found and r.waterline_found:
            t = r.water_height_mm / max(r.cup_height_mm, 1e-6)
            r_top = r.r_base_mm + (r.r_rim_mm - r.r_base_mm) * t
            from cupfiller.detection import cone_volume_ml
            v_meas = cone_volume_ml(r.water_height_mm, r.r_base_mm, r_top)
            out["meniscus_offset_mm"] = round(r.water_height_mm - r.water_height_mm * (args.known_ml / max(v_meas, 1e-6)), 2)
            print("meniscus_offset_mm =", out["meniscus_offset_mm"])
        cam.release()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("Da ghi", OUT)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
