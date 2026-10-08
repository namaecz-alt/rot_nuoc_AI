#!/usr/bin/env python3
"""Sinh (hoặc kiểm tra) file vector vàng: firmware/host_test/protocol_vectors.txt

    python tools/gen_protocol_vectors.py            # ghi file
    python tools/gen_protocol_vectors.py --check     # chỉ kiểm tra file có khớp không

File vector là "bản ghi trên dây" để PC (Python) và ESP32 (C++) đối chiếu từng byte.
Sửa giao thức -> chạy lại script này RỒI chạy tools/test_comms.py.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.protocol_vectors import render, self_check  # noqa: E402

OUT = os.path.join(ROOT, "firmware", "host_test", "protocol_vectors.txt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="không ghi, chỉ kiểm tra file hiện có")
    ap.add_argument("--out", default=OUT, help="đường dẫn file vector (mặc định %s)" % OUT)
    args = ap.parse_args()

    errors = self_check()
    if errors:
        print("LỖI: Python không khớp với bộ vector:")
        for e in errors:
            print("  -", e)
        return 1

    text = render()
    if args.check:
        try:
            with open(args.out, "r", encoding="utf-8") as fh:
                have = fh.read()
        except OSError as exc:
            print("LỖI: không đọc được %s (%s)" % (args.out, exc))
            return 1
        if have != text:
            print("LỖI: %s đã cũ! Chạy: python tools/gen_protocol_vectors.py" % args.out)
            return 1
        print("OK: %s khớp với bộ vector trong cupfiller/protocol_vectors.py" % args.out)
        return 0

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    n = sum(1 for line in text.splitlines() if line and not line.startswith("#"))
    print("Đã ghi %d vector vào %s" % (n, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
