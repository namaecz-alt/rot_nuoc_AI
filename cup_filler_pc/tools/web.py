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
    ap.add_argument("--port", default=None,
                    help="CHẾ ĐỘ ESP32: sim (giả lập) | COM5 | /dev/ttyUSB0 | tcp://host:port")
    ap.add_argument("--no-window", action="store_true",
                    help="(không dùng cho web) giữ cho giống run_pc.py")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.synthetic:
        cfg.set("camera.backend", "synthetic")
        cfg.set("control.pump.type", "sim")
    from cupfiller.webapp import WebApp

    if args.port:
        # chế độ ESP32: camera do phiên tự mở/đóng theo cảm biến cốc của ESP
        if args.synthetic or args.port == "sim":
            cfg.set("camera.backend", "synthetic")
        print("Chế độ ESP32 qua cổng %s — mở web rồi bấm 'Đặt cốc' để xem toàn bộ luồng."
              % args.port)
    WebApp(cfg, port=args.port).run()


if __name__ == "__main__":
    main()
