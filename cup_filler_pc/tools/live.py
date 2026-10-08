#!/usr/bin/env python3
"""Live view: xem camera + overlay nhận diện + điều khiển rót bằng bàn phím.

Phím:
  1..5      chọn preset (theo control.presets_ml)
  s         bắt đầu rót      e         dừng / về IDLE
  q         thoát
  d         bật/tắt overlay debug (mặt nạ cốc, dải sáng)

Chạy giả lập không cần phần cứng:  python3 tools/live.py --synthetic
Chạy với camera USB:               python3 tools/live.py --config config/settings.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.config import load_config                    # noqa: E402
from cupfiller.synthetic import SyntheticCupCamera, CupScene  # noqa: E402
from cupfiller.camera import open_camera                    # noqa: E402
from cupfiller.pump import open_pump                        # noqa: E402
from cupfiller.controller import FillController, State      # noqa: E402
from cupfiller.detection import draw_overlay                # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.synthetic:
        cfg.set("camera.backend", "synthetic")
        cfg.set("control.pump.type", "sim")
    scene = None
    if cfg.get("camera.backend", "") == "synthetic":
        scene = SyntheticCupCamera(CupScene(), fps=float(cfg.get("control.loop.fps", 15)))
    cam = open_camera(cfg, scene=scene)
    pump = open_pump(cfg, getattr(cam, "synthetic", None))
    ctl = FillController(cfg, cam, pump)

    dt = 1.0 / ctl.fps
    print("Phím: 1..5 preset | s rót | e dừng | d overlay | q thoát")
    while True:
        t0 = time.time()
        tel = ctl.tick(dt)
        frame = ctl.frame
        if frame is None:
            continue
        det = ctl.last_det
        lines = [
            "state: %s   preset: %g ml" % (tel["state"], tel["preset_ml"]),
            "muc nuoc: %.1f mm / dich %.1f mm   the tich ~%.0f ml" % (tel["h_mm"], tel["target_mm"], tel["volume_ml"]),
            "PWM %.2f   t %.1fs   %s" % (tel["pwm"], tel["elapsed_s"], tel["alert"]),
        ]
        vis = draw_overlay(frame, det, target_mm=tel["target_mm"] if tel["target_mm"] else None, text=lines)
        cv2.imshow("cup_filler", vis)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s"):
            ctl.start()
        elif key == ord("e"):
            ctl.stop()
        elif ord("1") <= key <= ord("5"):
            i = key - ord("1")
            if i < len(ctl.presets):
                ctl.set_preset(ctl.presets[i])
                print("preset ->", ctl.preset_ml, "ml")
        elapsed = time.time() - t0
        if elapsed < dt:
            time.sleep(dt - elapsed)
    pump.off()
    cam.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
