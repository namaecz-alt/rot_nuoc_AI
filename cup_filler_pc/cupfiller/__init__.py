"""cupfiller - Máy rót nước tự động: nhận diện cốc trong suốt + điều khiển mức bằng thị giác.

Các khối chính
---------------
synthetic  : tạo ảnh cốc trong suốt giả lập (để test/offline, không cần phần cứng)
detection  : tìm cốc (rim/base/bán kính) và đo vạch nước (waterline)
pump       : điều khiển bơm (GPIO thật / mô phỏng / bấm tay)
controller : máy trạng thái rót 2 pha (định lượng thô + vision tinh)
camera     : nguồn ảnh thống nhất (v4l2, picamera2, video, synthetic)
"""
from __future__ import annotations

__version__ = "1.0.0"

from .detection import CupDetection, CupDetector, WaterTracker  # noqa: F401
from .synthetic import CupScene, SyntheticCupCamera  # noqa: F401

__all__ = [
    "CupDetector",
    "CupDetection",
    "WaterTracker",
    "CupScene",
    "SyntheticCupCamera",
    "__version__",
]
