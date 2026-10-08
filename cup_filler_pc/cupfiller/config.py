"""Nạp cấu hình: hỗ trợ YAML, JSON và dict lồng nhau, truy cập kiểu thuộc tính.

Cách dùng:
    from cupfiller.config import load_config
    cfg = load_config("config/settings.yaml")
    print(cfg.camera.width, cfg.control.presets_ml)
    print(cfg.get("detection.strip.min_width_px", 40))
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict

__all__ = ["Config", "load_config", "DEFAULTS"]


class Config:
    """Bao một dict, cho phép truy cập cfg.a.b.c và cfg['a']['b']."""

    def __init__(self, data: Dict[str, Any]):
        object.__setattr__(self, "_data", data or {})

    # --- truy cập -----------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if name in data:
            return _wrap(data[name])
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__getattribute__(self, "_data")[name] = value

    def __getitem__(self, key: str) -> Any:
        return _wrap(object.__getattribute__(self, "_data")[key])

    def __setitem__(self, key: str, value: Any) -> None:
        object.__getattribute__(self, "_data")[key] = value

    def __contains__(self, key: str) -> bool:
        return key in object.__getattribute__(self, "_data")

    def __iter__(self):
        return iter(object.__getattribute__(self, "_data"))

    def keys(self):
        return object.__getattribute__(self, "_data").keys()

    def get(self, dotted: str, default: Any = None) -> Any:
        """cfg.get('detection.strip.min_width_px', 40)"""
        node: Any = object.__getattribute__(self, "_data")
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, dotted: str, value: Any) -> None:
        node = object.__getattribute__(self, "_data")
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(object.__getattribute__(self, "_data"))

    def __repr__(self) -> str:  # pragma: no cover
        return f"Config({object.__getattribute__(self, '_data')!r})"


def _wrap(value: Any) -> Any:
    return Config(value) if isinstance(value, dict) else value


# ---------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    "camera": {"backend": "synthetic", "device": 0, "width": 640, "height": 480, "fps": 30},
    "calibration": {
        "mm_per_px": 0.417,
        "optical_center_px": [320.0, 240.0],
        "meniscus_offset_mm": 0.0,
        "camera_offset_mm": 0.0,
        "in_flight_ml": 4.0,
    },
    "detection": {
        "cup_roi": None,
        "strip": {
            "auto_find": True,
            "x_range": [40, 600],
            "min_brightness": 150,
            "min_width_px": 40,
            "max_width_px": 480,
        },
        "cup": {
            "min_width_px": 28,
            "max_width_px": 260,
            "min_height_px": 45,
            "max_height_px": 420,
            "min_fill_ratio": 0.35,
        },
        "waterline": {
            "grad_threshold": 14,
            "min_support_ratio": 0.45,
            "search_margin_ratio": 0.92,
            "ransac_thresh_px": 2.5,
            "temporal_window": 5,
        },
    },
    "control": {
        "presets_ml": [100, 150, 200, 250, 300],
        "default_preset_ml": 200,
        "pump": {
            "type": "sim",
            "pin": 18,
            "active_low": False,
            "flow_curve": {"pwm": [0.35, 0.5, 0.7, 0.85, 1.0], "ml_per_s": [8, 16, 27, 34, 40]},
            "lag_s": 0.45,
            "min_pwm": 0.30,
            "max_pwm": 1.0,
        },
        "loop": {
            "fps": 15,
            "settle_s": 1.0,
            "coarse_margin_ratio": 0.85,
            "fine_band_mm": 8.0,
            "fine_pwm": 0.34,
            "topup_pulse_ml": 2.0,
            "topup_max_pulses": 5,
            "max_overfill_mm": 5.0,
            "timeout_s": 45,
            "check_cup_timeout_s": 30.0,
            "fine_blind_s": 3.0,
        },
    },
    # ---- ESP32 (cam bien coc + nut bam + mic + relay bom) qua UART ----
    "esp32": {
        "port": "COM5",           # Windows: COM5 | Linux: /dev/ttyUSB0 | macOS: /dev/cu.usbserial-*
        "baud": 115200,
        "confirm_frames": 3,      # so khung hinh thay coc lien tiep moi gui ACK,1
        "confirm_timeout_s": 5.0, # qua han -> bao ACK,0 (ESP32 khong cho bom)
        "keep_camera_open": True, # giu camera mo luc doi nut/mic (mo lai mat 1-3 s)
        "idle_release_s": 1.0,    # khong co coc bao lau thi tat camera
        "hb_period_s": 0.25,      # nhip gui ESP32 (watchdog ben do la 0.8 s)
    },
    "safety": {
        "max_volume_ml": 450,
        "require_cup": True,
        "cup_stable_frames": 4,
        "no_cup_grace_s": 1.5,
    },
    "logging": {"level": "INFO", "file": "logs/run.log"},
    "web": {"host": "0.0.0.0", "port": 8080},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | os.PathLike | None = None, **overrides: Any) -> Config:
    """Nạp config từ YAML/JSON. Thiếu file thì dùng mặc định (không ném lỗi)."""
    data: Dict[str, Any] = {}
    if path and os.path.exists(path):
        text = open(path, "r", encoding="utf-8").read()
        if str(path).endswith((".yaml", ".yml")):
            try:
                import yaml  # type: ignore

                data = yaml.safe_load(text) or {}
            except ImportError:
                data = _tiny_yaml(text)
        else:
            data = json.loads(text)
    elif path:
        raise FileNotFoundError(f"Không tìm thấy file cấu hình: {path}")

    merged = _deep_merge(DEFAULTS, data)
    merged = _deep_merge(merged, overrides)
    return Config(merged)


# ---------------------------------------------------------------------------
def _tiny_yaml(text: str) -> Dict[str, Any]:
    """Parser YAML tối giản (thụt lề 2 khoảng cách, không multi-line).

    Chỉ dùng khi Pi chưa cài PyYAML; hỗ trợ đúng cú pháp của settings.example.yaml.
    """

    def scalar(tok: str) -> Any:
        tok = tok.strip()
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1].strip()
            return [scalar(x) for x in inner.split(",")] if inner else []
        if tok in ("null", "~", ""):
            return None
        if tok in ("true", "True"):
            return True
        if tok in ("false", "False"):
            return False
        for cast in (int, float):
            try:
                return cast(tok)
            except ValueError:
                pass
        return tok.strip("'\"")

    root: Dict[str, Any] = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        raw = raw.split(" #", 1)[0].rstrip()
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if value.strip() == "":
            child: Dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = scalar(value)
    return root
