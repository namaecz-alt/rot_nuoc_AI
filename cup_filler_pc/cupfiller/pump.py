"""Điều khiển bơm: GPIO/PWM thật trên Raspberry Pi, hoặc mô phỏng, hoặc bấm tay.

Mọi loại bơm đều quy về hai hàm:  set_pwm(p) với p trong [0,1]  và  off().
Lưu lượng thực tế suy ra từ đường cong hiệu chuẩn flow_curve (pwm -> ml/s).
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

__all__ = ["Pump", "GpioPump", "SimPump", "ManualPump", "open_pump"]


class Pump:
    """Lớp cơ sở: biết đường cong lưu lượng và trạng thái PWM hiện tại."""

    def __init__(self, cfg=None):
        cfg = cfg or {}
        pc = cfg.get("control.pump", {}) if hasattr(cfg, "get") else {}
        curve = pc.get("flow_curve", {"pwm": [0.35, 1.0], "ml_per_s": [8.0, 40.0]})
        self.pwm_pts = np.asarray(curve.get("pwm", [0.35, 1.0]), float)
        self.flow_pts = np.asarray(curve.get("ml_per_s", [8.0, 40.0]), float)
        self.lag_s = float(pc.get("lag_s", 0.45))
        self.min_pwm = float(pc.get("min_pwm", 0.30))
        self.max_pwm = float(pc.get("max_pwm", 1.0))
        self.pwm = 0.0
        self._t_on = 0.0

    # ---- API ----------------------------------------------------------
    def flow_at(self, pwm: float) -> float:
        """Lưu lượng (ml/s) tại mức PWM, nội suy từ đường cong hiệu chuẩn."""
        if pwm <= 0:
            return 0.0
        return float(np.interp(pwm, self.pwm_pts, self.flow_pts))

    def pwm_for_flow(self, ml_per_s: float) -> float:
        if ml_per_s <= 0:
            return 0.0
        return float(np.clip(np.interp(ml_per_s, self.flow_pts, self.pwm_pts), self.min_pwm, self.max_pwm))

    def set_pwm(self, p: float) -> None:
        p = float(np.clip(p, 0.0, self.max_pwm))
        if p > 0 and p < self.min_pwm:
            p = self.min_pwm
        self.pwm = p
        self._hw_set(p)
        if p > 0:
            self._t_on += 0.0

    def off(self) -> None:
        self.pwm = 0.0
        self._hw_set(0.0)

    def _hw_set(self, p: float) -> None:  # pragma: no cover
        raise NotImplementedError

    # ---- tiện ích an toàn ---------------------------------------------
    def pulse(self, ml: float, fps: float = 50.0) -> float:
        """Bơm chính xác ~ml (mở vòng hở theo lưu lượng hiệu chuẩn). Trả về thời gian."""
        flow = self.flow_at(self.min_pwm)
        t = ml / max(flow, 1e-6)
        self.set_pwm(self.min_pwm)
        time.sleep(t)
        self.off()
        return t


class GpioPump(Pump):
    """Bơm/van qua MOSFET hoặc relay trên chân BCM, băm PWM bằng RPi.GPIO."""

    def __init__(self, cfg):
        super().__init__(cfg)
        pc = cfg.get("control.pump", {})
        self.pin = int(pc.get("pin", 18))
        self.active_low = bool(pc.get("active_low", False))
        import RPi.GPIO as GPIO  # chỉ tồn tại trên Raspberry Pi

        self.GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.pin, GPIO.OUT)
        self._pwm = GPIO.PWM(self.pin, 200)  # 200 Hz
        self._pwm.start(0)

    def _hw_set(self, p: float) -> None:
        duty = (1.0 - p) * 100.0 if self.active_low else p * 100.0
        self._pwm.ChangeDutyCycle(duty)

    def close(self) -> None:
        self._pwm.stop()
        self.GPIO.cleanup(self.pin)


class SimPump(Pump):
    """Bơm mô phỏng: đẩy lưu lượng (có trễ bậc 1) vào SyntheticCupCamera."""

    def __init__(self, cfg=None, syn_cam=None):
        super().__init__(cfg)
        self.syn_cam = syn_cam
        self._flow = 0.0

    def attach(self, syn_cam) -> None:
        self.syn_cam = syn_cam

    def _hw_set(self, p: float) -> None:
        self._flow = self.flow_at(p)
        if self.syn_cam is not None:
            self.syn_cam.set_flow(self._flow)

    @property
    def current_flow(self) -> float:
        return self._flow


class ManualPump(Pump):
    """Không phần cứng: chỉ log, dùng để dry-run thuật toán trên video/ảnh."""

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.log = []

    def _hw_set(self, p: float) -> None:
        self.log.append((time.time(), p))


def open_pump(cfg, syn_cam=None) -> Pump:
    kind = cfg.get("control.pump.type", "sim")
    if kind == "gpio":
        return GpioPump(cfg)
    if kind == "manual":
        return ManualPump(cfg)
    p = SimPump(cfg)
    if syn_cam is not None:
        p.attach(syn_cam)
    return p
