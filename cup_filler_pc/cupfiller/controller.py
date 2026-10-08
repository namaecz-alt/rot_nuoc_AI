r"""Máy trạng thái điều khiển rót nước - kiến trúc "3 lớp" bền vững với cốc trong suốt.

    IDLE -> CHECK_CUP -> PRIME -> COARSE -> FINE -> SETTLING -> (TOPUP <-> SETTLING) -> DONE
                                                   \________________________________/
                                    FAULT (timeout / mất cốc / quá thể tích an toàn)

Lớp 1 - ĐỊNH LƯỢNG THÔ (COARSE, mở vòng theo bơm):
    Tích phân lưu lượng hiệu chuẩn:  V_cmd += flow_est*dt.
    Không phụ thuộc hình học cốc, không phụ thuộc chất lượng ảnh khi rót mạnh.
    Đồng thời ghi lại các cặp mẫu (V_cmd, h_vision) khi vạch nước nhìn thấy được.

Lớp 2 - ÁNH XẠ ml -> mm HỌC TRỰC TIẾP (vào FINE):
    Hồi quy h = f(V) từ các mẫu -> target_mm = f(preset).
    Phép đo này tự thích ứng với HÌNH DÁNG THẬT của cốc (kể cả cốc méo, gân,
    đáy lõm) vì nó đo đáp ứng mực nước theo thể tích, không đo khuôn cốc qua ảnh
    (vốn bị khúc xạ làm méo silhouette).

Lớp 3 - VÒNG KÍN THỊ GIÁC (FINE + SETTLING + TOPUP):
    Bơm PWM nhỏ tới khi vạch nước chạm target_mm (trừ đoạn lead bù trễ),
    chờ lắng, đọc lại, bù thiếu bằng xung nhỏ. Thừa không hút được -> báo OVERFILL.

Lớp 4 - MODEL MỰC NƯỚC CỦA BẠN (tùy chọn, ``level_detector``):
    weights/muc_nuoc_yolo.pt nhìn ra dải mực nước ("0-", "30-", "60-", "90-" theo
    chiều cao cốc) ngay TRONG LÚC RÓT. Khi nước đã chạm mức người dùng chọn trên ESP
    (``target_ml``) -> ngắt bơm ngay, bỏ qua nhịp bù TOPUP. Đây là lớp kiểm tra độc
    lập với phép đong theo lưu lượng, nên rót đúng mức dù lưu lượng hiệu chuẩn lệch.

An toàn: timeout, mất cốc, giới hạn thể tích tuyệt đối, chặn target sát miệng cốc.
"""
from __future__ import annotations

import time
import types
from enum import Enum
from typing import Optional

import numpy as np

from .detection import CupDetection, CupDetector, WaterTracker, cone_volume_ml

__all__ = ["State", "FillController"]


class State(str, Enum):
    IDLE = "IDLE"
    CHECK_CUP = "CHECK_CUP"
    PRIME = "PRIME"
    COARSE = "COARSE"
    FINE = "FINE"
    SETTLING = "SETTLING"
    TOPUP = "TOPUP"
    DONE = "DONE"
    FAULT = "FAULT"


class FillController:
    def __init__(self, cfg, camera, pump, detector: Optional[CupDetector] = None,
                 level_detector=None):
        self.cfg = cfg
        self.cam = camera
        self.pump = pump
        self.det = detector or CupDetector(cfg)
        # lớp 4: model mực nước của người dùng (weights/muc_nuoc_yolo.pt); None = tắt
        self.level = level_detector
        loop = cfg.get("control.loop", {})
        self.fps = float(loop.get("fps", 15))
        self.settle_s = float(loop.get("settle_s", 0.8))
        self.coarse_margin = float(loop.get("coarse_margin_ratio", 0.85))
        self.fine_band_mm = float(loop.get("fine_band_mm", 8.0))
        self.fine_pwm = float(loop.get("fine_pwm", 0.34))
        self.topup_ml = float(loop.get("topup_pulse_ml", 2.0))
        self.topup_max = int(loop.get("topup_max_pulses", 5))
        self.max_over_mm = float(loop.get("max_overfill_mm", 4.0))
        self.timeout_s = float(loop.get("timeout_s", 45))
        self.safe = cfg.get("safety", {})
        self.max_vol = float(self.safe.get("max_volume_ml", 450))
        self.stable_n = int(self.safe.get("cup_stable_frames", 4))
        self.grace_s = float(self.safe.get("no_cup_grace_s", 1.5))
        self.in_flight_ml = float(cfg.get("calibration.in_flight_ml", 4.0))
        self.level_tol = float(cfg.get("control.level.reach_tolerance", 0.0))
        self.level_period = float(cfg.get("control.level.check_period_ms", 200)) / 1000.0
        # "mid": dừng khi nước vào dải chứa mức đã chọn | "lo": chỉ dừng khi chắc chắn
        # đã qua mức | "ml": model chỉ để hiển thị, dừng theo đong lưu lượng như cũ
        self.level_rule = str(cfg.get("control.level.stop_rule", "mid"))
        self.level_reading = None          # LevelReading gần nhất
        self.level_stop = False            # đã ngừng bơm vì model thấy đủ mức?
        self._t_level = 0.0

        self.tracker = WaterTracker(int(cfg.get("detection.waterline.temporal_window", 3)))
        self.presets = list(cfg.get("control.presets_ml", [100, 150, 200, 250, 300]))
        self.preset_ml = float(cfg.get("control.default_preset_ml", self.presets[-1] if self.presets else 200))

        self.state = State.IDLE
        self.alert = ""
        self.frame = None
        self.last_det: Optional[CupDetection] = None
        self.h_mm = 0.0
        self.target_mm = 0.0
        self.target_ml = 0.0
        self.volume_ml = 0.0
        self.elapsed = 0.0
        self._t_state = 0.0
        self._stable = 0
        self._no_cup_t = 0.0
        self._pulses = 0
        self._prev_box = None
        self._history: list = []
        self.final_report: dict = {}

        # hình học cốc đông cứng (dùng làm fallback & chặn an toàn)
        self.g_base = 0.0
        self.g_rim = 0.0
        self.g_h = 0.0
        # lớp 1: tích phân lưu lượng
        self._flow_est = 0.0
        self._vcmd = 0.0
        # lớp 2: mẫu (V, h) và hệ số hồi quy h = a + b*V (+ c*V^2)
        self._samples: list = []
        self._fit: Optional[np.ndarray] = None
        self._latency = 0.17

    # ------------------------------------------------------------------
    def set_preset(self, ml: float) -> None:
        self.preset_ml = float(ml)

    def start(self) -> None:
        if self.state in (State.COARSE, State.FINE, State.TOPUP, State.PRIME):
            return
        self.state = State.CHECK_CUP
        self.alert = ""
        self._t_state = 0.0
        self._stable = 0
        self._pulses = 0
        self.elapsed = 0.0
        self._vcmd = 0.0
        self._flow_est = 0.0
        self._samples = []
        self._fit = None
        self.tracker.reset()
        self.final_report = {}
        self.level_reading = None
        self.level_stop = False
        self._t_level = 0.0

    def stop(self, why: str = "") -> None:
        self.pump.off()
        if self.state not in (State.DONE,):
            self.state = State.IDLE
        if why:
            self.alert = why

    def _fault(self, why: str) -> None:
        self.pump.off()
        self.state = State.FAULT
        self.alert = why

    # ------------------------------------------------------------------
    def tick(self, dt: Optional[float] = None) -> dict:
        dt = dt or 1.0 / self.fps
        self.elapsed += dt
        self._t_state += dt

        ok, frame = self.cam.read()
        if not ok or frame is None:
            self.frame = None
            self.last_det = None
            # Camera loss while filling is a safety fault: stop the pump immediately.
            if self.state not in (State.IDLE, State.DONE, State.FAULT):
                self._fault("CAMERA_ERROR")
            return self.telemetry()
        self.frame = frame
        det = self.det.detect(frame, prev=self.last_det)
        self.last_det = det

        h_raw = det.water_height_mm if (det.found and det.waterline_found) else None
        h = self.tracker.update(h_raw, dt)
        self.h_mm = h if h is not None else self.h_mm

        # ước lượng lưu lượng thật (khâu trễ bậc nhất) + tích phân thể tích bơm
        cmd = self.pump.flow_at(self.pump.pwm)
        self._flow_est += (cmd - self._flow_est) * min(dt / max(self.pump.lag_s, 1e-3), 1.0)
        if self.state in (State.PRIME, State.COARSE, State.FINE, State.TOPUP):
            self._vcmd += self._flow_est * dt
            self.volume_ml = self._volume_from_h(self.h_mm)
            # ghi mẫu (V, h) cho lớp 2
            if (
                det.found
                and det.waterline_found
                and self.h_mm > 6.0
                and self.pump.pwm >= 0.3
                and self.state in (State.COARSE, State.FINE)
            ):
                # bù trễ đo: h tại thời điểm t ứng với thể tích đã bơm cách đó ~0.35 s
                v_s = self._vcmd - self._flow_est * 0.35
                self._samples.append((max(v_s, 0.0), self.h_mm))
                if len(self._samples) > 400:
                    self._samples.pop(0)

        # ---------------- lớp 4: model mực nước của người dùng ----------------
        # Chỉ chạy khi đang rót và model dùng được; có chu kỳ riêng để không nặng CPU.
        if (self.level is not None and getattr(self.level, "available", False)
                and self.state in (State.PRIME, State.COARSE, State.FINE, State.TOPUP)):
            self._t_level += dt
            if self.level_reading is None or self._t_level >= self.level_period:
                self._t_level = 0.0
                cup_box = (det.x0, det.y0, det.x1, det.y1) if det.found else None
                r = self.level.detect(frame, cup_box=cup_box)
                if r is not None:
                    self.level_reading = r

        # ---------------- an toàn cắt ngang ----------------
        if self.state in (State.PRIME, State.COARSE, State.FINE, State.TOPUP):
            if self.elapsed > self.timeout_s:
                self._fault("TIMEOUT: qua %g s chưa xong" % self.timeout_s)
                return self.telemetry()
            if not det.found:
                self._no_cup_t += dt
                self.pump.off()
                if self._no_cup_t > self.grace_s:
                    self._fault("CUP_REMOVED: mất cốc khi đang rót")
                    return self.telemetry()
            else:
                self._no_cup_t = 0.0
            if self._vcmd > self.max_vol * 1.15:
                self._fault("OVERVOLUME: bơm quá giới hạn an toàn %g ml" % self.max_vol)
                return self.telemetry()
            # model nhìn thấy nước đã chạm mức đã chọn -> ngừng bơm ngay
            if self._level_reached():
                self.pump.off()
                self.level_stop = True
                self.alert = "Tới mức đã chọn (%s)" % self.level_reading.text()
                self._goto(State.SETTLING)
                return self.telemetry()

        st = self.state
        if st == State.IDLE:
            self.pump.off()
        elif st == State.CHECK_CUP:
            self._state_check_cup(det)
        elif st == State.PRIME:
            if self._t_state >= 0.25:
                self._goto(State.COARSE)
        elif st == State.COARSE:
            self._state_coarse()
        elif st == State.FINE:
            self._state_fine()
        elif st == State.SETTLING:
            self._state_settling()
        elif st == State.TOPUP:
            if self._t_state >= self._topup_t:
                self.pump.off()
                self._goto(State.SETTLING)
        else:  # DONE / FAULT
            self.pump.off()

        self._history.append((self.elapsed, self.h_mm, self.pump.pwm, self.state.value))
        if len(self._history) > 20000:
            self._history.pop(0)
        return self.telemetry()

    # ------------------------------------------------------------------
    def cup_geom(self):
        """Hình học cốc đã đo, dạng đối tượng nhỏ để đổi dải mực nước -> ml."""
        return types.SimpleNamespace(cup_height_mm=self.g_h, r_base_mm=self.g_base,
                                     r_rim_mm=self.g_rim)

    def _level_reached(self) -> bool:
        """Model có đang thấy nước đã chạm ``target_ml`` chưa?"""
        if self.level is None or self.level_reading is None or self.g_h <= 0:
            return False
        try:
            return bool(self.level.reached(self.target_ml, self.cup_geom(), self.level_reading,
                                           tol=self.level_tol, mode=self.level_rule))
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _goto(self, st: State) -> None:
        self.state = st
        self._t_state = 0.0

    def _state_check_cup(self, det: CupDetection) -> None:
        if not det.found:
            self._stable = 0
            self.alert = "CHO DAT COC: chưa thấy cốc trong vùng nhìn"
            if self._t_state > 30:
                self.state = State.IDLE
                self._t_state = 0.0
            return
        moved = (
            self._prev_box is not None
            and abs(det.x0 - self._prev_box[0]) + abs(det.y0 - self._prev_box[1]) > 3
        )
        self._prev_box = (det.x0, det.y0, det.x1, det.y1)
        self._stable = 0 if moved else self._stable + 1
        if self._stable < self.stable_n:
            self.alert = "GIU COC YEN: đang chờ cốc đứng yên"
            return
        self.g_base, self.g_rim, self.g_h = det.r_base_mm, det.r_rim_mm, det.cup_height_mm
        self.target_ml = min(self.preset_ml, self.max_vol)
        # đích ban đầu từ hình học silhouette (sẽ được thay bằng ánh xạ học được);
        # chặn dưới miệng cốc 6 mm - ánh xạ học được sẽ tinh chỉnh sau
        self.target_mm = min(self._height_for_volume_geo(self.target_ml), self.g_h - 6.0)
        cap = cone_volume_ml(self.g_h, self.g_base * 1.15, self.g_rim * 1.15)
        if self.target_ml > cap:
            self._fault("CUP_TOO_SMALL: cốc không chứa nổi %g ml" % self.target_ml)
            return
        self.alert = ""
        self.pump.set_pwm(0.55)
        self._goto(State.PRIME)

    def _state_coarse(self) -> None:
        need = self.target_ml - self._vcmd - self.in_flight_ml
        if self._vcmd >= self.coarse_margin * self.target_ml:
            self._refit_target()
            self.pump.set_pwm(self.fine_pwm)
            self._goto(State.FINE)
            return
        desired_flow = max(need / 0.6, self.pump.flow_at(self.pump.min_pwm))
        self.pump.set_pwm(self.pump.pwm_for_flow(desired_flow))
        if self.last_det is not None and self.last_det.found and (
            self.h_mm > self.target_mm + self.fine_band_mm or self.h_mm > self.g_h - 3.0
        ):
            self.pump.off()
            self._goto(State.SETTLING)

    def _state_fine(self) -> None:
        self._refit_target(rate_limit=0.5)
        if self.h_mm > self.g_h - 3.0:
            self.alert = "CUP_FULL: cốc đã gần đầy"
            self.pump.off()
            self._goto(State.SETTLING)
            return
        # dừng sớm một đoạn lead bù nước còn trong bơm/ống + trễ đo
        lead_mm = (
            self.pump.flow_at(self.fine_pwm) * (0.7 * self.pump.lag_s + 0.10 + self._latency)
            / max(self._area_at_target(), 1e-6)
            * 1000.0
        )
        if self.h_mm >= self.target_mm - lead_mm:
            self.pump.off()
            self._goto(State.SETTLING)
        else:
            self.pump.set_pwm(self.fine_pwm)

    def _state_settling(self) -> None:
        if self._t_state < self.settle_s:
            return
        err = self.h_mm - self.target_mm
        if err > self.max_over_mm:
            self._fault("OVERFILL: vượt đích %.1f mm" % err)
            return
        if err < -1.0 and self._pulses < self.topup_max and not self.level_stop:
            self._pulses += 1
            pulse = min(self.topup_ml, max(abs(err) * self._area_at_target() / 1000.0, 0.8))
            self._topup_t = pulse / max(self.pump.flow_at(self.pump.min_pwm), 1e-6)
            self.pump.set_pwm(self.pump.min_pwm)
            self._goto(State.TOPUP)
            return
        self.final_report = {
            "preset_ml": self.target_ml,
            "stopped_by": ("model_muc_nuoc" if self.level_stop else "dong_theo_luu_luong"),
            "level_band": (self.level_reading.label if self.level_reading is not None else ""),
            "height_mm": round(self.h_mm, 2),
            "target_mm": round(self.target_mm, 2),
            "err_mm": round(err, 2),
            "volume_est_ml": round(self._volume_from_h(self.h_mm), 1),
            "pumped_ml": round(self._vcmd, 1),
            "pulses": self._pulses,
            "time_s": round(self.elapsed, 2),
        }
        self.pump.off()
        self._goto(State.DONE)

    # ------------------------------------------------------------------
    # lớp 2: hồi quy h = f(V)
    def _refit_target(self, rate_limit: Optional[float] = None) -> None:
        s = self._samples
        if len(s) < 6:
            return
        v = np.array([p[0] for p in s])
        h = np.array([p[1] for p in s])
        if v.max() - v.min() < 0.25 * self.target_ml:
            return
        deg = 2 if len(s) >= 14 else 1
        cf = np.polyfit(v, h, deg)
        d = np.polyder(cf)
        if np.polyval(d, v.min()) <= 0 or np.polyval(d, v.max()) <= 0:
            return                      # ánh xạ phải đồng biến

        tgt = float(np.polyval(cf, self.target_ml))
        if not (0.0 < tgt < self.g_h - 3.0):
            return
        self._fit = cf
        if rate_limit:
            tgt = self.target_mm + float(np.clip(tgt - self.target_mm, -rate_limit, rate_limit))
        self.target_mm = tgt

    def _volume_from_h(self, h_mm: float) -> float:
        """Thể tích (ml) ứng với mực nước h_mm: dùng ánh xạ học được nếu có."""
        if self._fit is not None and len(self._fit) == 2:
            a, b = self._fit
            if b > 1e-6:
                return float(np.clip((h_mm - a) / b, 0.0, self.max_vol * 1.2))
        if self.g_h > 0:
            h = float(np.clip(h_mm, 0.0, self.g_h))
            t = h / self.g_h
            r_top = self.g_base + (self.g_rim - self.g_base) * t
            return cone_volume_ml(h, self.g_base, r_top)
        return 0.0

    def _height_for_volume_geo(self, ml: float) -> float:
        lo, hi = 0.0, max(self.g_h, 1e-6)
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            t = mid / max(self.g_h, 1e-6)
            r_top = self.g_base + (self.g_rim - self.g_base) * t
            if cone_volume_ml(mid, self.g_base, r_top) < ml:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def _area_at_target(self) -> float:
        """Diện tích mặt thoáng (mm^2) tại đích: từ độ dốc ánh xạ học được."""
        if self._fit is not None:
            if len(self._fit) == 2 and self._fit[1] > 1e-6:
                return 1000.0 / self._fit[1]
            if len(self._fit) == 3:
                d = 2 * self._fit[0] * self.target_ml + self._fit[1]   # mm/ml
                if d > 1e-6:
                    return 1000.0 / d
        if self.g_h > 0:
            t = float(np.clip(self.target_mm / max(self.g_h, 1e-6), 0, 1))
            r = self.g_base + (self.g_rim - self.g_base) * t
            return float(np.pi * r * r)
        return 2800.0

    # ------------------------------------------------------------------
    def telemetry(self) -> dict:
        det = self.last_det
        return {
            "state": self.state.value,
            "alert": self.alert,
            "preset_ml": self.preset_ml,
            "presets": self.presets,
            "h_mm": round(self.h_mm, 2),
            "target_mm": round(self.target_mm, 2),
            "volume_ml": round(self.volume_ml, 1),
            "pumped_ml": round(self._vcmd, 1),
            "fill_pct": round(
                100.0 * self.h_mm / max(self.g_h, 1e-6) if self.g_h > 0 else 0.0, 1
            ),
            "pwm": round(self.pump.pwm, 3),
            "cup": bool(det.found) if det is not None else False,
            "waterline": bool(det.waterline_found) if det is not None else False,
            "cup_h_mm": round(self.g_h, 1),
            "level_band": (self.level_reading.label if self.level_reading is not None else ""),
            "level_conf": (round(float(self.level_reading.conf), 3)
                           if self.level_reading is not None else 0.0),
            "level_ml": (round(self.level.ml_now(self.cup_geom(), self.level_reading), 1)
                         if (self.level is not None and self.level_reading is not None
                             and self.g_h > 0) else 0.0),
            "level_target_ml": round(self.target_ml, 1),
            "level_stop": bool(self.level_stop),
            "level_rule": self.level_rule,
            "elapsed_s": round(self.elapsed, 2),
            "pulses": self._pulses,
            "report": self.final_report,
        }

    @property
    def history(self):
        return self._history
