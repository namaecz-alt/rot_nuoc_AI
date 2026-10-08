"""Nguồn ảnh thống nhất: webcam USB, Camera Module Pi, file video, hoặc giả lập.

Trên Windows thử DirectShow trước (thường ổn định hơn MSMF với webcam USB), sau
đó mới fallback Media Foundation/auto. USB camera được thử đọc frame lúc khởi tạo,
để báo lỗi sớm thay vì lặp warning liên tục trong UI.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional, Tuple

import cv2
import numpy as np

__all__ = ["Camera", "open_camera"]


class Camera:
    def __init__(self, backend: str, cfg=None, scene=None):
        self.backend = backend
        self.cfg = cfg
        self._cap = None
        self._syn = None
        self._prefetched: Optional[np.ndarray] = None
        self.backend_api = ""

        if backend == "v4l2":
            dev = cfg.get("camera.device", 0) if cfg else 0
            self._open_usb_camera(dev)
        elif backend == "picamera2":
            from picamera2 import Picamera2  # chỉ có trên Raspberry Pi

            self._cap = Picamera2()
            self._cap.configure(
                self._cap.create_still_configuration(
                    main={"size": (cfg.get("camera.width", 640), cfg.get("camera.height", 480))}
                )
            )
            self._cap.start()
        elif backend == "video":
            path = cfg.get("camera.source_file", "") if cfg else ""
            self._cap = cv2.VideoCapture(path)
        elif backend == "synthetic":
            from .synthetic import SyntheticCupCamera

            self._syn = scene or SyntheticCupCamera()
        else:
            raise ValueError(f"backend camera không hỗ trợ: {backend}")

    def _open_usb_camera(self, dev) -> None:
        """Mở webcam với backend phù hợp hệ điều hành và kiểm tra frame đầu tiên."""
        source = int(dev) if str(dev).isdigit() else dev
        if os.name == "nt":
            api_candidates = [("DirectShow", cv2.CAP_DSHOW), ("Media Foundation", cv2.CAP_MSMF), ("Auto", None)]
        elif sys.platform.startswith("linux"):
            api_candidates = [("V4L2", cv2.CAP_V4L2), ("Auto", None)]
        elif sys.platform == "darwin":
            api_candidates = [("AVFoundation", cv2.CAP_AVFOUNDATION), ("Auto", None)]
        else:
            api_candidates = [("Auto", None)]

        last_error = ""
        for api_name, api in api_candidates:
            # Thử độ phân giải cấu hình trước; nếu webcam không hỗ trợ, mở lại theo mặc định.
            for set_mode in (True, False):
                cap = None
                try:
                    cap = cv2.VideoCapture(source, api) if api is not None else cv2.VideoCapture(source)
                    if not cap.isOpened():
                        last_error = f"{api_name}: không mở được device {source}"
                        cap.release()
                        continue
                    if set_mode and self.cfg:
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.get("camera.width", 640))
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.get("camera.height", 480))
                        cap.set(cv2.CAP_PROP_FPS, self.cfg.get("camera.fps", 30))
                    # Bỏ khung cũ trong buffer nếu backend hỗ trợ; thất bại thì bỏ qua.
                    try:
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass

                    frame = None
                    for _ in range(5):
                        ok, candidate = cap.read()
                        if ok and candidate is not None and candidate.size:
                            frame = candidate
                            break
                        time.sleep(0.08)
                    if frame is not None:
                        self._cap = cap
                        self._prefetched = frame
                        self.backend_api = api_name
                        return
                    last_error = f"{api_name}: mở được nhưng không lấy được frame"
                    cap.release()
                except Exception as exc:
                    last_error = f"{api_name}: {exc}"
                    if cap is not None:
                        cap.release()

        raise RuntimeError(
            f"Không lấy được hình từ webcam {source}. Chi tiết: {last_error}. "
            "Hãy đóng ứng dụng Camera/Zoom/Teams, thử --camera 1, kiểm tra quyền Camera của Windows; "
            "nếu cần, dùng webcam khác."
        )

    # ------------------------------------------------------------------
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._syn is not None:
            return self._syn.read()
        if self.backend == "picamera2":
            frame = self._cap.capture_array()
            return True, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if self._prefetched is not None:
            frame, self._prefetched = self._prefetched, None
            return True, frame
        ok, frame = self._cap.read()
        if not ok and self.backend == "video":  # lặp lại video
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        return ok, frame if ok else None

    def release(self) -> None:
        if self._cap is not None and self.backend != "picamera2":
            self._cap.release()
        elif self._cap is not None:
            self._cap.stop()

    @property
    def synthetic(self):
        return self._syn


def open_camera(cfg, scene=None) -> Camera:
    backend = cfg.get("camera.backend", "synthetic")
    cam = Camera(backend, cfg, scene=scene)
    if backend in ("v4l2", "video") and cam._cap is not None and not cam._cap.isOpened():
        raise RuntimeError(f"Không mở được camera backend={backend}")
    return cam
