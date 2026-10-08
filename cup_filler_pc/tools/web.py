#!/usr/bin/env python3
"""Khởi động giao diện web. Xem hướng dẫn trong cupfiller/webapp.py."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.config import load_config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--synthetic", action="store_true", help="chạy giả lập, không cần phần cứng")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.synthetic:
        cfg.set("camera.backend", "synthetic")
        cfg.set("control.pump.type", "sim")
    from cupfiller.webapp import WebApp

    WebApp(cfg).run()


if __name__ == "__main__":
    main()
