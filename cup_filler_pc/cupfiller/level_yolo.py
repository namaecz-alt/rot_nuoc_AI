"""Nhận diện MỰC NƯỚC trong cốc bằng model YOLO do bạn tự train.

Model: ``weights/muc_nuoc_yolo.pt`` (YOLOv8n detect) có **4 lớp**, mỗi lớp là một *dải*
mực nước tính theo **chiều cao cốc** (không phải ml):

    "0-"   -> nước dưới 30% chiều cao cốc
    "30-"  -> 30% .. 60%
    "60-"  -> 60% .. 90%
    "90-"  -> trên 90%

Dùng vào hai việc:

1. **Đọc mực nước đang có** khi vừa nhận diện cốc (cốc đã có sẵn nước hay chưa).
2. **Trong lúc rót**: mỗi khung hình kiểm tra nước đã chạm mức người dùng chọn trên ESP
   chưa -> ``reached()`` -> ngừng bơm (chế độ PC điều khiển bơm).

Thiết kế để chạy được ở cả nơi **không có** ultralytics/torch:
    * ``available`` trả False và mọi thứ tự động tắt, không làm hỏng luồng cũ;
    * có thể truyền ``predictor`` (hàm tự viết) để kiểm thử logic mà không cần model thật.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .detection import cone_volume_ml

__all__ = ["LEVEL_BANDS", "LEVEL_ORDER", "LevelReading", "WaterLevelDetector",
           "ml_at_frac"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LEVEL_MODEL = os.path.join(ROOT, "weights", "muc_nuoc_yolo.pt")

#: Dải mực nước của từng lớp, tính theo tỉ lệ **chiều cao cốc** (0..1).
#: "30-" nghĩa là nước từ 30% tới 60% chiều cao cốc (xem firmware/README §3).
LEVEL_BANDS: Dict[str, Tuple[float, float]] = {
    "0-": (0.00, 0.30),
    "30-": (0.30, 0.60),
    "60-": (0.60, 0.90),
    "90-": (0.90, 1.00),
}
LEVEL_ORDER: Tuple[str, ...] = ("0-", "30-", "60-", "90-")
_ALIASES = {
    "0": "0-", "30": "30-", "60": "60-", "90": "90-",
    "muc0": "0-", "muc30": "30-", "muc60": "60-", "muc90": "90-",
}


def _norm_label(label: str) -> str:
    """Chuẩn hoá tên lớp về dạng trong ``LEVEL_BANDS`` ('0-', '30-', ...)."""
    s = str(label).strip().lower().replace(" ", "").replace("_", "")
    if s in LEVEL_BANDS:
        return s
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits in _ALIASES:
        return _ALIASES[digits]
    if digits in LEVEL_BANDS:
        return digits + "-"
    return s


def ml_at_frac(frac: float, cup) -> float:
    """Thể tích (ml) ứng với mực nước ở ``frac`` lần chiều cao cốc.

    ``cup`` là ``CupDetection`` (hoặc vật bất kỳ có ``cup_height_mm``, ``r_base_mm``,
    ``r_rim_mm``) - dùng cùng công thức nón cụt với phần nhận diện cốc.
    """
    f = float(min(max(frac, 0.0), 1.0))
    h_mm = float(getattr(cup, "cup_height_mm", 0.0)) * f
    if h_mm <= 0:
        return 0.0
    r_base = float(getattr(cup, "r_base_mm", 0.0))
    r_rim = float(getattr(cup, "r_rim_mm", 0.0))
    r_top = r_base + (r_rim - r_base) * f
    return cone_volume_ml(h_mm, r_base, r_top)


@dataclass
class LevelReading:
    """Một lần model đọc được mực nước."""

    label: str                       # '0-' | '30-' | '60-' | '90-'
    lo: float                        # mép DƯỚI của dải theo tỉ lệ chiều cao cốc
    hi: float                        # mép TRÊN của dải
    conf: float = 0.0
    box: Optional[Tuple[int, int, int, int]] = None

    @property
    def frac_mid(self) -> float:
        return 0.5 * (self.lo + self.hi)

    @property
    def index(self) -> int:
        return LEVEL_ORDER.index(self.label) if self.label in LEVEL_ORDER else -1

    def as_dict(self) -> Dict:
        return {"label": self.label, "lo": self.lo, "hi": self.hi,
                "conf": round(float(self.conf), 3), "box": self.box}

    def text(self) -> str:
        return "%s (%d-%d%% chiều cao cốc, conf %.2f)" % (
            self.label, round(self.lo * 100), round(self.hi * 100), self.conf)


class WaterLevelDetector:
    """Bọc model YOLO mực nước; tự tắt nếu thiếu file/thư viện.

    Tham số
    -------
    cfg        : cấu hình (đọc ``vision.level_*``), có thể None.
    weights    : đường dẫn model, mặc định ``weights/muc_nuoc_yolo.pt``.
    conf       : ngưỡng tin cậy, mặc định 0.35 (hoặc ``vision.level_conf``).
    predictor  : hàm ``frame -> [(nhãn, conf, (x0,y0,x1,y1)), ...]`` - dùng để kiểm thử
                 mà không cần ultralytics.
    """

    def __init__(self, cfg=None, weights: Optional[str] = None, conf: Optional[float] = None,
                 imgsz: Optional[int] = None, predictor: Optional[Callable] = None,
                 enabled: Optional[bool] = None):
        self.cfg = cfg
        self.weights = weights or (cfg.get("vision.level_model", DEFAULT_LEVEL_MODEL)
                                   if cfg else DEFAULT_LEVEL_MODEL)
        self.conf = float(conf if conf is not None else
                          (cfg.get("vision.level_conf", 0.35) if cfg else 0.35))
        self.imgsz = int(imgsz if imgsz is not None else
                         (cfg.get("vision.level_imgsz", 512) if cfg else 512))
        self._predictor = predictor
        if enabled is None:
            enabled = bool(cfg.get("vision.level_enable", True)) if cfg else True
        self.enabled = bool(enabled)
        self._model = None
        self._load_tried = False
        self.last: Optional[LevelReading] = None
        self.last_error = ""

    # ------------------------------------------------------------------
    @property
    def has_predictor(self) -> bool:
        return self._predictor is not None

    @property
    def available(self) -> bool:
        """Có dùng được không: có predictor, hoặc có model + ultralytics."""
        if not self.enabled:
            return False
        if self._predictor is not None:
            return True
        if not os.path.exists(self.weights):
            return False
        return self._load() is not None

    def _load(self):                                     # pragma: no cover - cần torch
        if self._model is not None or self._load_tried:
            return self._model
        self._load_tried = True
        try:
            from ultralytics import YOLO

            self._model = YOLO(self.weights)
        except Exception as exc:
            self._model = None
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
        return self._model

    def names(self) -> List[str]:                        # pragma: no cover - cần torch
        m = self._load()
        if m is None:
            return []
        n = getattr(m, "names", None) or {}
        return [str(v) for v in (n.values() if isinstance(n, dict) else n)]

    # ------------------------------------------------------------------
    def detect(self, frame, cup_box: Optional[Sequence[int]] = None) -> Optional[LevelReading]:
        """Đọc mực nước trong một khung hình; trả ``None`` nếu không thấy gì."""
        raw = self._predict(frame)
        best = self._pick(raw, cup_box)
        if best is None:
            return None
        label, conf, box = best
        key = _norm_label(label)
        if key not in LEVEL_BANDS:
            self.last_error = "lớp lạ: %r" % label
            return None
        lo, hi = LEVEL_BANDS[key]
        self.last = LevelReading(label=key, lo=lo, hi=hi, conf=float(conf), box=box)
        return self.last

    def _predict(self, frame) -> List[Tuple[str, float, Optional[Tuple[int, int, int, int]]]]:
        if self._predictor is not None:
            return list(self._predictor(frame) or [])
        model = self._load()                             # pragma: no cover - cần torch
        if model is None:
            return []
        try:
            res = model.predict(frame, conf=self.conf, imgsz=self.imgsz, verbose=False)[0]
            names = getattr(res, "names", {}) or {}
            out = []
            for b in res.boxes:
                cls = int(b.cls[0]) if hasattr(b.cls, "__len__") else int(b.cls)
                label = str(names.get(cls, cls) if isinstance(names, dict) else cls)
                xyxy = tuple(int(v) for v in (b.xyxy[0].tolist() if hasattr(b.xyxy, "tolist")
                                              else list(b.xyxy[0])))
                out.append((label, float(b.conf[0] if hasattr(b.conf, "__len__") else b.conf), xyxy))
            return out
        except Exception as exc:                          # pragma: no cover
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return []

    @staticmethod
    def _pick(raw, cup_box: Optional[Sequence[int]]):
        """Chọn hộp tốt nhất: ưu tiên hộp nằm trong cốc, rồi tới conf cao nhất."""
        if not raw:
            return None
        cands = list(raw)
        if cup_box is not None and len(cup_box) == 4:
            x0, y0, x1, y1 = cup_box

            def inside(item):
                b = item[2]
                if not b:
                    return False
                cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                return (x0 - 4) <= cx <= (x1 + 4) and (y0 - 4) <= cy <= (y1 + 4)

            inside_c = [c for c in cands if inside(c)]
            if inside_c:
                cands = inside_c
        return max(cands, key=lambda c: float(c[1]))

    # ------------------------------------------------------------------
    def ml_at(self, frac: float, cup) -> float:
        return ml_at_frac(frac, cup)

    def ml_now(self, cup, reading: Optional[LevelReading] = None) -> float:
        """Thể tích nước hiện có theo mép DƯỚI của dải (ước lượng thận trọng)."""
        r = reading or self.last
        return ml_at_frac(r.lo, cup) if r is not None else 0.0

    def reached(self, target_ml: float, cup, reading: Optional[LevelReading] = None,
                tol: float = 0.0, mode: str = "mid") -> bool:
        """Nước đã chạm mức cần rót chưa?

        Model chỉ biết nước đang nằm trong dải nào, nên chọn cách so:

        * ``"mid"`` (mặc định) - dừng ngay khi nước **vào dải chứa mức đã chọn**
          (ước lượng bằng điểm giữa dải). Đây là cách đúng cho các mốc 30/60/90:
          chọn mốc 60% thì dừng khi model thấy lớp "60-" (nước đã chạm vạch 60%).
        * ``"lo"`` - chỉ dừng khi **chắc chắn** đã qua mức (mép dưới của dải >= mức):
          an toàn hơn nhưng có thể rót dư tới một dải.
        * ``"off"``/``"ml"`` - không dừng theo model (model chỉ để hiển thị/ghi log).

        ``tol`` cho phép châm chước theo tỉ lệ (0.1 = cho phép non 10%).
        """
        r = reading or self.last
        if r is None or str(mode).lower() in ("off", "ml", "none", "tat"):
            return False
        frac = r.frac_mid if str(mode).lower() != "lo" else r.lo
        return ml_at_frac(frac, cup) >= float(target_ml) * (1.0 - float(tol))
