"""Cầu nối PC <-> ESP32: biến gói UART thành sự kiện, và sự kiện thành lệnh.

Đây là "não phía máy tính": nó giữ trạng thái của ESP, phát hiện cốc mới đặt, gọi bộ
nhận diện (do ``cupfiller.session`` lo), rồi **gửi CUP_OK xuống để ESP mở khoá nút/mic**.
Ngoài ra nó:

  * trả lời HELLO bằng HELLO_ACK (báo PC đã sẵn sàng + preset + năng lực),
  * nhận PRESET_SELECTED / BUTTON_EVENT / VOICE_EVENT / FILL_* và ghi log,
  * nhận AUDIO_CHUNK -> ``cupfiller.voice_pc`` nhận dạng -> chọn preset + ra lệnh bơm,
  * gửi PING định kỳ và theo dõi mất kết nối,
  * ở chế độ PC điều khiển bơm: cấp ``RemotePump`` để ``FillController`` chạy vòng kín
    thị giác y như khi điều khiển GPIO trực tiếp.

Vòng đời::

    bridge = EspBridge(cfg, on_cup_placed=..., on_cup_removed=..., on_event=...)
    while running:
        bridge.tick(dt)          # xử lý gói vào/ra, heartbeat
        ...
    bridge.close()
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Sequence

import numpy as np

from . import protocol as P
from .serial_link import SerialEsp, open_link
from .voice_pc import VoiceRecognizer, VoiceResult

__all__ = ["EspStatus", "EspBridge", "RemotePump", "MODE_FLAGS"]

# cờ cho SET_MODE (định nghĩa chung trong protocol.py)
MODE_FLAG_PC_CONTROLS_PUMP = P.MODE_PC_CONTROLS_PUMP
MODE_FLAG_VOICE_STREAM = P.MODE_VOICE_STREAM


@dataclass
class EspStatus:
    """Ảnh chụp trạng thái ESP32 (từ gói STATUS gần nhất)."""

    state: int = int(P.EspState.BOOT)
    state_name: str = "BOOT"
    cup_present: bool = False
    pc_confirmed: bool = False
    pumping: bool = False
    poured_ml: int = 0
    target_ml: int = 0
    max_ml: int = 0
    uptime_ms: int = 0
    flags: int = 0
    t_rx: float = 0.0
    fw_version: int = 0
    caps: int = 0
    presets: List[int] = field(default_factory=list)
    flow_ml_s: float = 0.0
    link_ok: bool = False
    n_frames: int = 0

    @property
    def unlocked(self) -> bool:
        """Nút/mic trên ESP đã được mở khoá chưa (chỉ sau khi PC gửi CUP_OK)."""
        return bool(self.pc_confirmed) or self.state == int(P.EspState.MANUAL)

    def as_dict(self) -> Dict:
        d = dict(self.__dict__)
        d["unlocked"] = self.unlocked
        return d


class RemotePump:
    """'Bơm' nằm trên ESP32: PWM ở đây chính là duty của relay, gửi qua gói PUMP_SET.

    Nhờ lớp này, ``FillController`` (vòng kín thị giác) không cần biết bơm ở đâu -
    nó vẫn gọi ``set_pwm()``/``off()`` như thường, còn ESP lo phần cứng + an toàn.
    """

    def __init__(self, bridge: "EspBridge", flow_ml_s: float = 40.0,
                 min_duty: float = 0.08, lag_s: float = 0.30, syn_cam=None):
        self.bridge = bridge
        # Khi chạy DEMO bằng camera giả lập: đổ luôn lưu lượng vào cảnh giả lập để
        # vòng kín thị giác thấy mực nước dâng (vẫn mô phỏng đúng đường đi của lệnh).
        self.syn_cam = syn_cam
        self.min_pwm = float(min_duty)
        self.max_pwm = 1.0
        self.lag_s = float(lag_s)
        self.pwm = 0.0
        self.pwm_pts = np.array([self.min_pwm, 1.0])
        # relay + bơm màng: lưu lượng ~ tỉ lệ với duty (đã kiểm chứng bằng đo thực tế)
        self.flow_pts = np.array([flow_ml_s * self.min_pwm, flow_ml_s])

    def flow_at(self, pwm: float) -> float:
        if pwm <= 0:
            return 0.0
        return float(np.interp(pwm, self.pwm_pts, self.flow_pts))

    def pwm_for_flow(self, ml_per_s: float) -> float:
        if ml_per_s <= 0:
            return 0.0
        return float(np.clip(np.interp(ml_per_s, self.flow_pts, self.pwm_pts),
                             self.min_pwm, self.max_pwm))

    def set_pwm(self, p: float) -> None:
        p = float(np.clip(p, 0.0, 1.0))
        if 0 < p < self.min_pwm:
            p = self.min_pwm
        self.pwm = p
        if self.syn_cam is not None:
            try:
                self.syn_cam.set_flow(self.flow_at(p))
            except Exception:
                pass
        self.bridge.pump_set(p)

    def off(self) -> None:
        self.pwm = 0.0
        if self.syn_cam is not None:
            try:
                self.syn_cam.set_flow(0.0)
            except Exception:
                pass
        self.bridge.pump_set(0.0)

    def pulse(self, ml: float, fps: float = 50.0) -> float:   # tương thích API Pump
        flow = self.flow_at(self.min_pwm)
        t = ml / max(flow, 1e-6)
        self.set_pwm(self.min_pwm)
        time.sleep(t)
        self.off()
        return t


class EspBridge:
    """Quản lý một ESP32 qua UART: trạng thái, sự kiện, lệnh, nhận dạng giọng nói."""

    def __init__(self, cfg=None, link: Optional[SerialEsp] = None, port: Optional[str] = None,
                 baud: int = 921600, log: Optional[Callable[[str], None]] = None,
                 on_cup_placed: Optional[Callable[[dict], None]] = None,
                 on_cup_removed: Optional[Callable[[dict], None]] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 tick_simulator: bool = True):
        self.cfg = cfg
        self.log = log or (lambda msg: print(msg, flush=True))
        self.on_cup_placed = on_cup_placed
        self.on_cup_removed = on_cup_removed
        self.on_event = on_event
        self.tick_simulator = tick_simulator

        presets = list(cfg.get("control.presets_ml", [100, 150, 200, 250, 300])) if cfg else []
        self.presets = presets or [100, 150, 200, 250, 300]
        port = port or (cfg.get("esp.port", "sim") if cfg else "sim")
        baud = int(cfg.get("esp.baud", baud) if cfg else baud)
        timeout = float(cfg.get("esp.link_timeout_s", 3.0) if cfg else 3.0)
        sim_kwargs = {}
        if cfg is not None:
            sim_kwargs = {
                "presets": tuple(self.presets),
                "flow_ml_s": float(cfg.get("esp.sim_flow_ml_s", 40.0)),
                "voice_mode": int(cfg.get("esp.voice_mode", 2)),
                "pc_controls_pump": bool(cfg.get("esp.pc_controls_pump", False)),
            }
        self.link = link or open_link(port, baud=baud, log=self.log, link_timeout_s=timeout,
                                      sim_kwargs=sim_kwargs)
        self.port = port
        self.simulator = getattr(self.link, "simulator", None)

        # --- nhận dạng giọng nói phía PC ---
        self.voice = VoiceRecognizer(
            presets=self.presets,
            sample_rate=int(cfg.get("esp.audio_sample_rate", 16000) if cfg else 16000),
            engine=str(cfg.get("voice.engine", "auto") if cfg else "auto"),
            vosk_model_path=str(cfg.get("voice.vosk_model", "") if cfg else ""),
            log=self.log,
        )
        self.voice_auto_start = bool(cfg.get("voice.auto_start", True) if cfg else True)
        self.last_voice: Optional[VoiceResult] = None

        # --- chính sách ---
        self.pc_controls_pump = bool(cfg.get("esp.pc_controls_pump", False) if cfg else False)
        self.heartbeat_s = float(cfg.get("esp.ping_period_s", 1.0) if cfg else 1.0)
        self.pc_caps = (P.CAP_VISION | P.CAP_CUP_INFO |
                        (P.CAP_VOICE_ASR if self.voice.engine != "none" else 0) |
                        (P.CAP_PC_CLOSED_LOOP if self.pc_controls_pump else 0))

        self.status = EspStatus(presets=list(self.presets))
        self.reset_pour_max_ml = float(cfg.get("safety.max_volume_ml", 450) if cfg else 450)
        self.events: Deque[Dict] = deque(maxlen=400)
        self.preset_index = int(cfg.get("esp.default_preset_index", 2) if cfg else 2)
        self.preset_ml = float(self.presets[min(self.preset_index, len(self.presets) - 1)])
        self.t_last_ping = 0.0
        self.t_last_ping_sim_ms = -1e9
        self.t_hello_rx = 0.0
        self.t_fill_start: Optional[float] = None
        self.n_cup_events = 0
        self.n_reject = 0
        self.remote_pump = RemotePump(self, flow_ml_s=self.status.flow_ml_s or 40.0,
                                      min_duty=float(cfg.get("esp.min_duty_pct", 8) / 100.0) if cfg else 0.08)
        self.auto_confirm = bool(cfg.get("esp.auto_hello_ack", True) if cfg else True)

    # ==================================================================
    #  Sự kiện -> hàng đợi + callback
    # ==================================================================
    def _emit(self, name: str, fields: Optional[dict] = None, **kw) -> None:
        ev = {"t": time.time(), "name": name, "fields": fields or {}}
        ev.update(kw)
        self.events.append(ev)
        if self.on_event is not None:
            try:
                self.on_event(name, ev)
            except Exception as exc:                      # pragma: no cover
                self.log("[esp] on_event lỗi: %s" % exc)

    # ==================================================================
    #  Vòng chạy
    # ==================================================================
    def tick(self, dt: float = 0.05) -> None:
        """Gọi đều đặn (5-50 Hz) từ vòng lặp UI: nhận gói, gửi nhịp tim, nhận dạng mic."""
        if self.simulator is not None and self.tick_simulator:
            self.simulator.tick(dt)
        for frame, fields in self.link.poll():
            self._handle(frame, fields)
        self.status.link_ok = self.link.link_ok
        # nhịp tim + hỏi thăm ESP.
        #  - phần cứng thật: theo ĐỒNG HỒ THỰC (ESP cũng đếm thời gian thực)
        #  - bộ giả lập chạy nhanh hơn thời gian thực: dùng ĐỒNG HỒ CỦA ESP, nếu không
        #    watchdog mất liên lạc của ESP sẽ "nổ" giữa chừng khi mô phỏng
        now = time.time()
        if self.simulator is not None:
            sim_ms = float(getattr(self.simulator, "uptime_ms", 0.0))
            if (sim_ms - self.t_last_ping_sim_ms) >= self.heartbeat_s * 1000.0:
                self.t_last_ping_sim_ms = sim_ms
                self.link.send_ping(int(sim_ms) & 0xFFFFFFFF)
        elif (now - self.t_last_ping) >= self.heartbeat_s:
            self.t_last_ping = now
            self.link.send_ping(int(now * 1000) & 0xFFFFFFFF)

    # ==================================================================
    #  Xử lý từng gói tin
    # ==================================================================
    def _handle(self, frame: P.Frame, fields: dict) -> None:
        msg = int(frame.msg)
        self.status.t_rx = time.time()
        self.status.n_frames += 1
        name = P.Msg(msg).name if msg in set(int(m) for m in P.Msg) else "0x%02X" % msg

        if msg == P.Msg.HELLO:
            self.status.fw_version = int(fields.get("version") or 0)
            self.status.caps = int(fields.get("caps") or 0)
            self.status.presets = list(fields.get("presets") or self.presets)
            self.status.flow_ml_s = float(fields.get("flow_ml_s") or 0.0)
            if self.status.flow_ml_s > 0:
                self.remote_pump.flow_pts = np.array([self.remote_pump.min_pwm * self.status.flow_ml_s,
                                                      self.status.flow_ml_s])
            self.t_hello_rx = time.time()
            caps = self.pc_caps
            self.link.send_hello_ack(P.PROTO_VERSION, caps, self.status.presets or self.presets)
            self._send_mode_flags()
            self._emit(name, fields=fields)
            return

        if msg == P.Msg.CUP_PLACED:
            self.n_cup_events += 1
            self.status.cup_present = True
            self.status.pc_confirmed = False
            self._emit(name, fields=fields)
            if self.on_cup_placed is not None:
                try:
                    self.on_cup_placed(fields)
                except Exception as exc:                  # pragma: no cover
                    self.log("[esp] on_cup_placed lỗi: %s" % exc)
            return

        if msg == P.Msg.CUP_REMOVED:
            self.status.cup_present = False
            self.status.pc_confirmed = False
            self.status.pumping = False
            reason = int(fields.get("reason") or 0)
            self.log("[ESP] %s" % P.stop_reason_text(reason))
            if reason == int(P.StopReason.SENSOR_FAULT):
                self.log("[ESP] LỖI CẢM BIẾN: kiểm tra dây/jack HC-SR04 rồi gửi 'tare' hoặc "
                         "'mode' để ESP chạy lại")
            self._emit(name, fields=fields)
            if self.on_cup_removed is not None:
                try:
                    self.on_cup_removed(fields)
                except Exception as exc:                  # pragma: no cover
                    self.log("[esp] on_cup_removed lỗi: %s" % exc)
            return

        if msg == P.Msg.STATUS:
            self.status.state = int(fields.get("state") or 0)
            self.status.state_name = fields.get("state_name") or "?"
            self.status.flags = int(fields.get("flags") or 0)
            self.status.cup_present = bool(fields.get("cup_present"))
            self.status.pc_confirmed = bool(fields.get("pc_confirmed"))
            self.status.pumping = bool(fields.get("pumping"))
            self.status.poured_ml = int(fields.get("poured_ml") or 0)
            self.status.target_ml = int(fields.get("target_ml") or 0)
            self.status.max_ml = int(fields.get("max_ml") or 0)
            self.status.uptime_ms = int(fields.get("uptime_ms") or 0)
            self._emit(name, fields=fields)
            return

        if msg == P.Msg.PRESET_SELECTED:
            idx = int(fields.get("index") or 0)
            ml = float(fields.get("ml") or 0)
            src = int(fields.get("source") or 0)
            self.preset_index, self.preset_ml = idx, ml
            self._emit(name, fields=fields)
            self.log("[ESP] chọn mức %g ml (nguồn: %s)" % (ml, ["nút", "mic", "PC", "auto"][min(src, 3)]))
            return

        if msg == P.Msg.FILL_STARTED:
            self.status.pumping = True
            self.status.target_ml = int(fields.get("ml") or 0)
            self.t_fill_start = time.time()
            self._emit(name, fields=fields)
            return

        if msg == P.Msg.FILL_PROGRESS:
            self.status.poured_ml = int(fields.get("poured_ml") or 0)
            self._emit(name, fields=fields)
            return

        if msg == P.Msg.FILL_DONE:
            self.status.pumping = False
            self.status.poured_ml = int(fields.get("poured_ml") or 0)
            elapsed = float(fields.get("elapsed_ms") or 0) / 1000.0
            self._emit(name, fields=fields)
            code = int(fields.get("status") or 0)
            self.log("[ESP] kết thúc lượt rót: %d ml / đích %d ml trong %.1fs (%s)"
                     % (self.status.poured_ml, int(fields.get("target_ml") or 0), elapsed,
                        P.stop_reason_text(code)))
            return

        if msg == P.Msg.BUTTON_EVENT:
            self._emit(name, fields=fields)
            return

        if msg == P.Msg.VOICE_EVENT:
            self.last_voice = self.voice.analyze(fallback_peaks=int(fields.get("n_peaks") or 0)) \
                if self.voice.has_segment else self._voice_from_event(fields)
            self._emit(name, fields=fields)
            self._after_voice()
            return

        if msg == P.Msg.AUDIO_CHUNK:
            self.voice.feed(fields)
            if fields.get("end"):
                self.last_voice = self.voice.analyze()
                self._after_voice()
            return

        if msg == P.Msg.ERROR:
            self.log("[ESP] BÁO LỖI mã %s: %s" % (fields.get("code"), fields.get("text")))
            self._emit(name, fields=fields)
            return

        if msg == P.Msg.LOG:
            self.log("[ESP] %s" % fields.get("text", ""))
            return

        if msg == P.Msg.PONG:
            self._emit(name, fields=fields)
            return

        self._emit(name, fields=fields)

    def _voice_from_event(self, fields: dict) -> VoiceResult:
        n = int(fields.get("n_peaks") or 0)
        res = VoiceResult(engine="esp", n_peaks=n, confidence=int(fields.get("confidence") or 0))
        if n <= 0:
            res.reason = "ESP không nghe ra tiếng nào"
            return res
        idx = n - 1
        if idx >= len(self.presets):
            res.reason = "%d tiếng > %d preset" % (n, len(self.presets))
            return res
        res.ok = True
        res.index, res.ml = idx, float(self.presets[idx])
        res.text = "%d tiếng -> %g ml" % (n, res.ml)
        return res

    def _after_voice(self) -> None:
        res = self.last_voice
        if res is None:
            return
        self._emit("voice_result", result=res.as_dict())
        if not res.ok:
            if res.reason:
                self.log("[mic] chưa hiểu: %s" % res.reason)
            return
        self.log("[mic] %s -> mức %g ml (tin cậy %d%%, %s)"
                 % (res.text, res.ml, res.confidence, res.engine))
        self.set_preset(res.ml, res.index)
        if self.voice_auto_start and self.status.unlocked:
            self.start_fill(res.ml, mode=(P.FillMode.PC_CLOSED_LOOP if self.pc_controls_pump
                                          else P.FillMode.ESP_OPEN_LOOP),
                            source=P.Source.VOICE)

    # ==================================================================
    #  Lệnh gửi xuống ESP
    # ==================================================================
    def _send_mode_flags(self) -> None:
        on = 0
        if self.pc_controls_pump:
            on |= MODE_FLAG_PC_CONTROLS_PUMP
        self.link.send(P.Msg.SET_MODE, P.encode_set_mode(on, 0))

    def confirm_cup(self, det=None, *, rim_r_mm: float = 0.0, base_r_mm: float = 0.0,
                    height_mm: float = 0.0, max_ml: float = 0.0, confidence: float = 1.0,
                    label: str = "", flags: int = 0) -> None:
        """PC ĐÃ NHẬN DIỆN XONG CỐC -> gửi CUP_OK, ESP mở khoá nút bấm + mic."""
        if det is not None:
            rim_r_mm = float(getattr(det, "r_rim_mm", rim_r_mm) or rim_r_mm)
            base_r_mm = float(getattr(det, "r_base_mm", base_r_mm) or base_r_mm)
            height_mm = float(getattr(det, "cup_height_mm", height_mm) or height_mm)
            conf = float(getattr(det, "confidence", confidence) or confidence)
            confidence = max(0.05, min(1.0, conf))
        cap = max_ml if max_ml > 0 else self.max_ml_from_cup(height_mm, base_r_mm, rim_r_mm)
        cap = min(cap, float(self.status.max_ml) if self.status.max_ml else self.reset_pour_max_ml,
                  self.reset_pour_max_ml)
        self.link.send_cup_ok(rim_r_mm, base_r_mm, height_mm, cap, confidence, flags, label)
        self.status.pc_confirmed = True
        self.status.max_ml = int(cap)
        self._emit("cup_ok_sent", rim_r_mm=rim_r_mm, base_r_mm=base_r_mm, height_mm=height_mm,
                   max_ml=cap, confidence=confidence, label=label)
        self.log("[PC] đã xác nhận cốc: cao %.0f mm, miệng Ø%.0f mm, chứa tối đa %.0f ml -> ESP mở khoá nút/mic"
                 % (height_mm, rim_r_mm * 2, cap))

    @staticmethod
    def max_ml_from_cup(height_mm: float, base_r_mm: float, rim_r_mm: float,
                        safety_mm: float = 8.0, margin: float = 1.10) -> float:
        """Thể tích tối đa nên rót cho một cốc (chừa `safety_mm` dưới miệng cốc)."""
        h = max(0.0, float(height_mm) - safety_mm)
        if h <= 0:
            return 0.0
        r0, r1 = float(base_r_mm), float(rim_r_mm)
        vol = 3.141592653589793 * h * (r0 * r0 + r0 * r1 + r1 * r1) / 3.0 / 1000.0
        return float(max(0.0, vol * margin))

    def reject_cup(self, reason: int = P.RejectReason.NOT_FOUND, text: str = "") -> None:
        """Không nhận diện được cốc -> ESP chưa mở khoá, báo người dùng đặt lại."""
        self.n_reject += 1
        self.status.pc_confirmed = False
        self.link.send_cup_reject(reason, text)
        self._emit("cup_reject_sent", reason=int(reason), text=text)
        if text:
            self.log("[PC] chưa nhận diện được cốc: %s" % text)

    def set_preset(self, ml: float, index: Optional[int] = None) -> None:
        presets = self.status.presets or self.presets
        if index is None:
            index = min(range(len(presets)), key=lambda i: abs(float(presets[i]) - float(ml)))
        self.preset_index, self.preset_ml = int(index), float(ml)
        self.link.send_set_preset(int(index), int(round(ml)), P.Source.PC)

    def start_fill(self, ml: Optional[float] = None, mode: Optional[int] = None,
                   source: int = P.Source.PC, timeout_ms: int = 0) -> None:
        ml = float(ml if ml is not None else self.preset_ml)
        if mode is None:
            mode = P.FillMode.PC_CLOSED_LOOP if self.pc_controls_pump else P.FillMode.ESP_OPEN_LOOP
        self.link.send_start_fill(int(round(ml)), int(mode), int(source), timeout_ms)
        self._emit("start_fill_sent", ml=ml, mode=int(mode))

    def stop_fill(self, reason: int = P.StopReason.PC_REQUEST) -> None:
        self.link.send_stop_fill(reason)
        self._emit("stop_fill_sent", reason=int(reason))

    def pump_set(self, duty: float, duration_ms: int = 0) -> None:
        self.link.send_pump_set(float(duty), int(duration_ms))

    def send_params(self, flow_ml_s: Optional[float] = None, max_ml: Optional[int] = None,
                    timeout_s: Optional[int] = None, pulse_ms: Optional[int] = None) -> None:
        self.link.send_set_params(flow_ml_s, max_ml, timeout_s, pulse_ms)

    def cmd(self, cmd: int, arg: int = 0) -> None:
        self.link.send_cmd(int(cmd), int(arg))

    @property
    def remote_pump_adapter(self) -> RemotePump:
        """Bơm 'ảo' trỏ tới ESP, dùng được ngay với ``FillController``."""
        return self.remote_pump

    # ---- tiện ích cho chương trình chính / giao diện web --------------
    @property
    def summary(self) -> Dict:
        st = self.status.as_dict()
        st.update({
            "port": self.port,
            "link": self.link.stats,
            "preset_ml": self.preset_ml,
            "pc_controls_pump": self.pc_controls_pump,
            "voice": self.last_voice.as_dict() if self.last_voice else None,
            "voice_engine": self.voice.engine,
            "n_cup_events": self.n_cup_events,
        })
        return st

    def close(self) -> None:
        try:
            if self.status.pumping:
                self.stop_fill(P.StopReason.PC_REQUEST)
        except Exception:
            pass
        self.link.close()
