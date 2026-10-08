"""GIẢ LẬP ESP32: mô hình hoá đúng máy trạng thái của firmware (firmware/esp32_cup_filler).

Nhờ lớp này, toàn bộ luồng "đặt cốc -> PC nhận diện -> mở khoá nút/mic -> bơm" chạy
thử và kiểm thử tự động được **không cần bo mạch thật**:

  * ``tools/esp_sim.py``    - cửa sổ giả lập có phím bấm (đặt/nhấc cốc, nhấn nút, nói)
  * ``tools/esp_cli.py``    - nối tới bản giả lập (hoặc ESP thật) để xem/gửi gói tin
  * ``tools/test_comms.py`` - dùng trong bài kiểm thử tự động end-to-end

Dùng trong tiến trình::

    from cupfiller.serial_link import SerialEsp, LoopbackLink
    a, b = LoopbackLink.pair()
    link = SerialEsp(a, auto_thread=False)
    sim = Esp32Simulator()
    sim.attach(b)                       # ESP -> PC qua b
    sim.place_cup()                     # mô phỏng đặt cốc lên cảm biến
    for _ in range(50):
        sim.tick(0.02)                  # ESP chạy
        for frame, fields in link.poll():
            ...                         # PC xử lý

Các hằng số ở đây phải khớp ``firmware/esp32_cup_filler/config.h``: đây là bản sao
"bằng giấy" của firmware, dùng để kiểm thử giao thức + logic mà không cần phần cứng.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields as dc_fields
from typing import Dict, List, Optional, Sequence, Tuple

from . import protocol as P
from .serial_link import LinkBase

__all__ = ["EspSimConfig", "Esp32Simulator"]


@dataclass
class EspSimConfig:
    """Tham số mô phỏng (đối chiếu ``config.h`` của firmware)."""

    presets: Sequence[int] = (100, 150, 200, 250, 300)
    preset_index_default: int = 2          # 200 ml
    n_buttons: int = 5

    # --- cảm biến cốc (HC-SR04) ---
    baseline_mm: float = 150.0             # khoảng cách khi khay trống
    cup_min_height_mm: float = 30.0        # thấp hơn mức này = không tính là cốc
    cup_max_distance_mm: float = 400.0
    cup_debounce_ms: int = 300             # ổn định bấy lâu mới báo "đã đặt cốc"
    cup_height_mm: float = 80.0            # chiều cao cốc giả lập

    # --- bơm / relay (tích cực mức CAO) ---
    flow_ml_s: float = 40.0                # lưu lượng hiệu chuẩn khi relay bật 100%
    in_flight_ml: float = 4.0              # nước còn trên đường ống khi ngắt bơm
    max_fill_ml: int = 500                 # trần tuyệt đối (ESP tự cắt)
    pour_timeout_ms: int = 60000
    min_duty_pct: int = 8                  # duty nhỏ nhất còn bơm được
    duty_cycle_ms: int = 2000              # chu kỳ băm relay khi PC đặt duty < 100%
    finish_zero_duty_ms: int = 300         # PC giữ duty 0 bấy lâu -> kết thúc lượt rót

    # --- nút bấm (tích cực mức THẤP, INPUT_PULLUP) ---
    debounce_ms: int = 25
    long_press_ms: int = 1500
    estop_hold_ms: int = 2000
    button_starts_pour: bool = True        # nhấn ngắn = chọn mức + bơm luôn

    # --- mic ---
    voice_mode: int = 2                    # 0=tắt, 1=nhận dạng trên ESP, 2=gửi PCM lên PC
    voice_auto_start: bool = True
    pc_controls_pump: bool = False          # True: nút/mic CHỈ báo mức, PC ra lệnh bơm
    audio_sample_rate: int = 16000
    audio_chunk_samples: int = 96

    # --- liên lạc ---
    status_period_ms: int = 500
    progress_period_ms: int = 200
    hello_period_ms: int = 2000            # gửi lại HELLO khi PC chưa trả lời
    cup_retry_ms: int = 2000               # gửi lại CUP_PLACED khi chưa được xác nhận
    wait_pc_timeout_ms: int = 15000        # quá lâu không thấy CUP_OK -> báo lỗi
    link_timeout_ms: int = 3000            # không nhận gói nào từ PC -> coi như mất liên lạc
    link_stops_pump: bool = True
    allow_manual_when_link_lost: bool = False


class Esp32Simulator:
    """Máy trạng thái ESP32 + mô hình bơm/cảm biến (tất định, điều khiển bằng tick)."""

    def __init__(self, cfg: Optional[EspSimConfig] = None, **kwargs):
        if cfg is None:
            cfg = EspSimConfig()
        if kwargs:
            merged = {f.name: getattr(cfg, f.name) for f in dc_fields(EspSimConfig)}
            merged.update(kwargs)
            cfg = EspSimConfig(**merged)
        self.cfg = cfg

        self.link: Optional[LinkBase] = None
        self.decoder = P.FrameDecoder()
        self.seq = 0
        self.uptime_ms = 0.0
        self.t_last_hello = -1e9
        self.t_last_status = -1e9
        self.t_last_progress = -1e9
        self.t_last_rx = -1e9
        self.t_cup_change: Optional[float] = None
        self.t_wait_pc: Optional[float] = None
        self.t_last_cup_retry = -1e9
        self.t_fill_start = -1e9
        self.t_zero_duty_start: Optional[float] = None
        self.pulse_end_ms: Optional[float] = None
        self.pulse_duty = 0
        self.last_cup_ok: Optional[Dict] = None

        # --- vật lý ---
        self.cup_present = False               # cờ THÔ của cảm biến (chưa debounce)
        self.cup_sensor = False                # cảm biến thấy vật (đã debounce)
        self.pc_confirmed = False              # PC đã gửi CUP_OK
        self.cup_height_mm = float(cfg.cup_height_mm)
        self.poured_ml = 0.0
        self.relay_on = False
        self.duty_pct = 0
        self.preset_index = cfg.preset_index_default
        self.target_ml = float(list(cfg.presets)[cfg.preset_index_default])
        self.max_ml = float(cfg.max_fill_ml)
        self.fill_mode = int(P.FillMode.ESP_OPEN_LOOP)
        self.state = int(P.EspState.BOOT)
        self.error_text = ""
        self.n_frames_rx = 0

        self.events: List[Tuple[float, str, dict]] = []
        self.relay_history: List[Tuple[float, bool]] = []

    # ==================================================================
    #  Cổng truyền (hướng ESP -> PC)
    # ==================================================================
    def attach(self, link: LinkBase) -> None:
        self.link = link

    def tx(self, msg: int, payload: bytes) -> None:
        if self.link is None:
            return
        self.seq = (self.seq + 1) & 0xFF
        self.link.write(P.encode(int(msg), payload, self.seq))

    def log(self, text: str) -> None:
        self.tx(P.Msg.LOG, text.encode("utf-8")[: P.MAX_PAYLOAD])

    def _event(self, name: str, **kw) -> None:
        self.events.append((self.uptime_ms, name, kw))

    def event_names(self) -> List[str]:
        return [e[1] for e in self.events]

    def state_name(self) -> str:
        try:
            return P.STATE_NAMES.get(P.EspState(self.state), str(self.state))
        except ValueError:
            return str(self.state)

    # ==================================================================
    #  Sự kiện vật lý (người dùng / môi trường)
    # ==================================================================
    def place_cup(self, height_mm: Optional[float] = None) -> None:
        if height_mm is not None:
            self.cup_height_mm = float(height_mm)
        self.cup_present = True          # cờ thô của cảm biến (chưa debounce)
        self.t_cup_change = self.uptime_ms
        self._event("cup_placed_sensor", height_mm=self.cup_height_mm)

    def remove_cup(self) -> None:
        self.cup_present = False
        self.t_cup_change = self.uptime_ms
        self._event("cup_removed_sensor")

    def press_button(self, index: int, hold_ms: int = 120) -> None:
        """Nhấn nút (hold >= estop_hold_ms = NHẤN GIỮ để dừng khẩn cấp)."""
        self._event("button", index=index, hold_ms=hold_ms)
        if hold_ms >= self.cfg.estop_hold_ms:
            if self._pump_running():
                self._stop_pour(P.StopReason.BUTTON_ESTOP, "NHAN GIU NUT = DUNG KHAN")
            self.tx(P.Msg.BUTTON_EVENT, P.encode_button_event(index, P.BTN_ESTOP, hold_ms, self.state))
            return
        if hold_ms >= self.cfg.long_press_ms:
            self.tx(P.Msg.BUTTON_EVENT, P.encode_button_event(index, P.BTN_LONG, hold_ms, self.state))
            self.select_preset(index, P.Source.BUTTON, start=False)
            return
        self.tx(P.Msg.BUTTON_EVENT, P.encode_button_event(index, P.BTN_SHORT, hold_ms, self.state))
        self.select_preset(index, P.Source.BUTTON, start=self.cfg.button_starts_pour)

    def speak(self, index: int, confidence: int = 85) -> None:
        """Người dùng NÓI mức nước thứ `index` (0-based).

        voice_mode=1: ESP tự đếm số tiếng rồi chọn mức + bơm.
        voice_mode=2: ESP gửi PCM lên PC, PC nhận dạng rồi gửi lệnh xuống.
        """
        self._event("speak", index=index, conf=confidence)
        bursts = index + 1
        if self.cfg.voice_mode == 1:
            self.tx(P.Msg.VOICE_EVENT, P.encode_voice_event(
                P.VOICE_CLAPS, index, confidence, bursts, 800, 1200))
            self.select_preset(index, P.Source.VOICE, start=self.cfg.voice_auto_start)
        else:
            self._stream_audio(bursts, confidence)

    def _stream_audio(self, bursts: int, confidence: int) -> None:
        """Sinh PCM 16 kHz (mỗi 'tiếng' = burst 200 ms, cách nhau 150 ms im lặng)."""
        sr = self.cfg.audio_sample_rate
        chunk = self.cfg.audio_chunk_samples
        burst_n = int(0.20 * sr)
        gap_n = int(0.15 * sr)
        samples: List[int] = []
        for i in range(bursts):
            for n in range(burst_n):
                samples.append(int(9000 * math.sin(2 * math.pi * 220 * n / sr)))
            if i < bursts - 1:
                samples.extend([0] * gap_n)
        samples.extend([0] * int(0.25 * sr))
        total = len(samples)
        seq8 = 0
        for pos in range(0, total, chunk):
            part = samples[pos : pos + chunk]
            flags = (P.AU_START if pos == 0 else 0) | (P.AU_END if pos + chunk >= total else 0)
            self.tx(P.Msg.AUDIO_CHUNK, P.encode_audio_chunk(seq8, flags, part))
            seq8 = (seq8 + 1) & 0xFF
        self.tx(P.Msg.VOICE_EVENT, P.encode_voice_event(
            P.VOICE_UNKNOWN, 0, confidence, bursts, 800, int(1000 * total / sr)))

    # ==================================================================
    #  Chọn mức nước
    # ==================================================================
    def preset_ml(self, index: int) -> int:
        presets = list(self.cfg.presets)
        return int(presets[max(0, min(int(index), len(presets) - 1))])

    def select_preset(self, index: int, source: int, start: bool) -> None:
        if self.state == P.EspState.POURING:
            self.log(" BO QUA: dang bom")
            return
        if not self.unlocked:
            self.log(" CHUA MO KHOA: dat coc va cho PC xac nhan truoc (state=%s)" % self.state_name())
            return
        self.preset_index = max(0, min(int(index), len(self.cfg.presets) - 1))
        self.target_ml = float(self.preset_ml(self.preset_index))
        self.tx(P.Msg.PRESET_SELECTED, P.encode_preset_selected(
            self.preset_index, int(self.target_ml), int(source)))
        self._event("preset_selected", index=self.preset_index, ml=self.target_ml, source=int(source))
        if start and not self.cfg.pc_controls_pump:
            self.start_pour(self.target_ml, self.fill_mode, source)
        elif start:
            self.log(" cho PC ra lenh bom (che do PC dieu khien)")

    @property
    def unlocked(self) -> bool:
        """Nút bấm / mic có được phép dùng không (chỉ sau khi PC xác nhận cốc)."""
        if self.state == P.EspState.MANUAL:
            return True
        return bool(self.pc_confirmed) and self.cup_sensor

    # ==================================================================
    #  Bơm (relay tích cực mức CAO)
    # ==================================================================
    def _pump_running(self) -> bool:
        return bool(self.relay_on)

    def start_pour(self, ml: float, mode: int = int(P.FillMode.ESP_OPEN_LOOP),
                   source: int = int(P.Source.PC)) -> None:
        if self.state == P.EspState.POURING:
            self.log(" DANG BOM ROI")
            return
        if not (self.pc_confirmed or self.state == P.EspState.MANUAL):
            self.log(" KHONG BOM: chua duoc PC xac nhan coc")
            return
        if not self.cup_sensor:
            self.log(" KHONG BOM: khong thay coc")
            return
        target = min(float(ml), self.max_ml)
        if target <= 0:
            target = self.target_ml
        self.target_ml = target
        self.fill_mode = int(mode)
        self.poured_ml = 0.0
        self.t_fill_start = self.uptime_ms
        self.t_zero_duty_start = None
        self.pulse_end_ms = None
        self.duty_pct = 100
        self.relay_on = True
        self.relay_history.append((self.uptime_ms, True))
        self._to(P.EspState.POURING)
        self.tx(P.Msg.FILL_STARTED, P.encode_fill_started(
            int(self.target_ml), self.fill_mode, int(source), int(self.max_ml)))
        self._event("fill_started", ml=self.target_ml, mode=self.fill_mode, source=int(source))

    def _stop_pour(self, status: int, note: str = "") -> None:
        self.relay_on = False
        self.duty_pct = 0
        self.pulse_end_ms = None
        self.relay_history.append((self.uptime_ms, False))
        elapsed = int(max(0.0, self.uptime_ms - self.t_fill_start)) if self.t_fill_start > 0 else 0
        self.tx(P.Msg.FILL_DONE, P.encode_fill_done(
            int(round(self.poured_ml)), int(self.target_ml), int(status), elapsed))
        if note:
            self.log(" " + note)
        self._event("fill_done", poured=round(self.poured_ml, 2), status=int(status))
        self._to(P.EspState.DONE if (self.cup_sensor and self.pc_confirmed) else P.EspState.IDLE)

    def set_duty(self, duty: float, duration_ms: int = 0) -> None:
        """PUMP_SET: PC điều khiển trực tiếp relay (chế độ vòng kín thị giác).

        duty >= min_duty_pct mới bơm được (relay cơ không băm được ở tần số cao, nên
        firmware băm chậm theo ``duty_cycle_ms``). duration_ms > 0 = xung có thời hạn.
        """
        pct = int(round(max(0.0, min(1.0, duty)) * 100))
        if 0 < pct < self.cfg.min_duty_pct:
            pct = self.cfg.min_duty_pct
        if duration_ms:
            self.pulse_end_ms = self.uptime_ms + duration_ms
            self.pulse_duty = pct
        self.duty_pct = pct
        if pct == 0:
            self.relay_on = False
            if self.state == P.EspState.POURING and self.t_zero_duty_start is None:
                self.t_zero_duty_start = self.uptime_ms
        else:
            self.t_zero_duty_start = None
            if self.state == P.EspState.POURING:
                self.relay_on = True

    # ==================================================================
    #  Trạng thái & gói tin định kỳ
    # ==================================================================
    def _to(self, state: int) -> None:
        if self.state != state:
            self._event("state", was=int(self.state), now=int(state))
        self.state = int(state)
        if state == P.EspState.WAIT_PC and self.t_wait_pc is None:
            self.t_wait_pc = self.uptime_ms

    def flags(self) -> int:
        f = 0
        if self.cup_sensor:
            f |= P.FL_CUP_PRESENT
        if self.pc_confirmed:
            f |= P.FL_PC_CONFIRMED
        if self._pump_running():
            f |= P.FL_PUMPING
        if self.cfg.voice_mode:
            f |= P.FL_VOICE_ACTIVE
        if (self.uptime_ms - self.t_last_rx) < self.cfg.link_timeout_ms:
            f |= P.FL_LINK_OK
        if self.state == P.EspState.MANUAL:
            f |= P.FL_MANUAL
        return f

    def send_status(self) -> None:
        self.tx(P.Msg.STATUS, P.encode_status(
            self.state, self.flags(), int(round(self.poured_ml)),
            int(self.target_ml), int(self.max_ml), int(self.uptime_ms)))

    def send_progress(self) -> None:
        pct = int(round(100.0 * self.poured_ml / max(self.target_ml, 1e-6)))
        self.tx(P.Msg.FILL_PROGRESS, P.encode_fill_progress(
            int(round(self.poured_ml)), max(0, min(pct, 200)), int(self.target_ml), self.state))

    def send_hello(self) -> None:
        caps = P.ESP_CAP_BUTTONS | P.ESP_CAP_US_SENSOR
        if self.cfg.voice_mode:
            caps |= P.ESP_CAP_VOICE
        self.tx(P.Msg.HELLO, P.encode_hello(P.PROTO_VERSION, caps, list(self.cfg.presets),
                                            self.cfg.flow_ml_s))
        self.t_last_hello = self.uptime_ms

    # ==================================================================
    #  Nhận & xử lý gói từ PC
    # ==================================================================
    def pump_to_pc(self) -> None:
        """Đọc dữ liệu PC gửi tới (không cần luồng nền)."""
        if self.link is None:
            return
        data = self.link.read(65536)
        if not data:
            return
        for frame in self.decoder.feed(data):
            self.n_frames_rx += 1
            self.t_last_rx = self.uptime_ms
            self.on_frame(frame)

    def on_frame(self, frame: P.Frame) -> None:
        fields = P.decode(frame)
        self._event("rx", msg=int(frame.msg), fields=fields)
        msg = int(frame.msg)
        if msg == P.Msg.PING:
            self.tx(P.Msg.PONG, P.encode_pong(int(self.uptime_ms), self.state, self.flags()))
        elif msg == P.Msg.HELLO_ACK:
            self._event("pc_ready", presets=fields.get("presets"))
        elif msg == P.Msg.CUP_OK:
            self.last_cup_ok = fields
            self.pc_confirmed = True
            cap = float(fields.get("max_ml") or 0.0)
            if cap > 0:
                self.max_ml = min(self.max_ml, cap)
            self.t_wait_pc = None
            self._to(P.EspState.READY)
            self.send_status()
        elif msg == P.Msg.CUP_REJECT:
            self.last_cup_ok = None
            self.pc_confirmed = False
            self._to(P.EspState.WAIT_PC)
            self.error_text = fields.get("text") or "PC khong nhan dien duoc coc"
            self.send_status()
        elif msg == P.Msg.SET_PRESET:
            self.preset_index = int(fields.get("index") or 0)
            self.target_ml = float(fields.get("ml") or self.target_ml)
        elif msg == P.Msg.START_FILL:
            self.start_pour(float(fields.get("ml") or self.target_ml),
                            int(fields.get("mode") or 0), int(fields.get("source") or 2))
        elif msg == P.Msg.STOP_FILL:
            if self._pump_running() or self.state == P.EspState.POURING:
                self._stop_pour(int(fields.get("reason") or P.StopReason.PC_REQUEST), "PC dung bom")
        elif msg == P.Msg.PUMP_SET:
            self.set_duty(float(fields.get("duty") or 0.0), int(fields.get("duration_ms") or 0))
        elif msg == P.Msg.SET_MODE:
            on = int(fields.get("on") or 0)
            off = int(fields.get("off") or 0)
            if on & P.MODE_PC_CONTROLS_PUMP:
                self.cfg.pc_controls_pump = True
            if off & P.MODE_PC_CONTROLS_PUMP:
                self.cfg.pc_controls_pump = False
            self._event("set_mode", pc_controls=self.cfg.pc_controls_pump)
        elif msg == P.Msg.SET_PARAMS:
            if fields.get("flow_ml_s"):
                self.cfg.flow_ml_s = float(fields["flow_ml_s"])
            if fields.get("max_ml"):
                self.max_ml = float(fields["max_ml"])
            if fields.get("timeout_s"):
                self.cfg.pour_timeout_ms = int(fields["timeout_s"]) * 1000
        elif msg == P.Msg.CMD:
            self._on_cmd(int(fields.get("cmd") or 0))
        elif msg == P.Msg.ACK_PC:
            pass

    def _on_cmd(self, cmd: int) -> None:
        if cmd == P.Cmd.TARE:
            self.log(" tare xong: baseline_mm=%.1f" % self.cfg.baseline_mm)
        elif cmd == P.Cmd.DISARM:
            self.pc_confirmed = False
            self.last_cup_ok = None
            if self._pump_running():
                self._stop_pour(P.StopReason.PC_REQUEST, "disarm")
            self._to(P.EspState.WAIT_PC if self.cup_sensor else P.EspState.IDLE)
        elif cmd == P.Cmd.SELF_TEST:
            self.log(" self-test: bat relay 300 ms")
        elif cmd == P.Cmd.RESET_STATE:
            self.pc_confirmed = False
            self.last_cup_ok = None
            self.t_wait_pc = None
            self._to(P.EspState.IDLE)

    # ==================================================================
    #  Vòng chạy
    # ==================================================================
    def tick(self, dt: float) -> None:
        self.uptime_ms += dt * 1000.0
        self.pump_to_pc()
        self._update_sensor()
        self._update_pump(dt)
        self._update_link()
        if (self.uptime_ms - self.t_last_status) >= self.cfg.status_period_ms:
            self.send_status()
            self.t_last_status = self.uptime_ms
        if self.state == P.EspState.POURING and \
           (self.uptime_ms - self.t_last_progress) >= self.cfg.progress_period_ms:
            self.send_progress()
            self.t_last_progress = self.uptime_ms
        if self.state == P.EspState.BOOT and (self.uptime_ms - self.t_last_hello) > 300:
            self.send_hello()
            self._to(P.EspState.IDLE)        # khởi động xong, chờ cốc
        elif self.state == P.EspState.WAIT_PC and \
                (self.uptime_ms - self.t_last_hello) >= self.cfg.hello_period_ms:
            self.send_hello()

    # ---- cảm biến cốc -------------------------------------------------
    def _update_sensor(self) -> None:
        if self.t_cup_change is None:
            return
        if (self.uptime_ms - self.t_cup_change) < self.cfg.cup_debounce_ms:
            return
        if self.cup_present and not self.cup_sensor:
            self.cup_sensor = True
            self.t_wait_pc = self.uptime_ms
            self._to(P.EspState.WAIT_PC)
            self.tx(P.Msg.CUP_PLACED, P.encode_cup_placed(
                int(max(0.0, self.cfg.baseline_mm - self.cup_height_mm)),
                int(self.cfg.baseline_mm), int(self.cup_height_mm), 0x01, int(self.uptime_ms)))
            self._event("cup_placed_reported", height_mm=self.cup_height_mm)
            self.t_last_cup_retry = self.uptime_ms
        elif not self.cup_present and self.cup_sensor:
            self.cup_sensor = False
            self.pc_confirmed = False
            self.last_cup_ok = None
            if self._pump_running() or self.state == P.EspState.POURING:
                self._stop_pour(P.StopReason.CUP_REMOVED, "MAT COC -> NGAT BOM")
            self.tx(P.Msg.CUP_REMOVED, P.encode_cup_removed(P.StopReason.CUP_REMOVED,
                                                            int(self.uptime_ms)))
            self.t_wait_pc = None
            self._to(P.EspState.IDLE)
            self._event("cup_removed_reported")
        # PC chưa xác nhận: nhắc lại CUP_PLACED, quá lâu thì báo lỗi
        if self.cup_sensor and not self.pc_confirmed and self.state == P.EspState.WAIT_PC:
            if (self.uptime_ms - self.t_last_cup_retry) >= self.cfg.cup_retry_ms:
                self.t_last_cup_retry = self.uptime_ms
                self.tx(P.Msg.CUP_PLACED, P.encode_cup_placed(
                    int(max(0.0, self.cfg.baseline_mm - self.cup_height_mm)),
                    int(self.cfg.baseline_mm), int(self.cup_height_mm), 0x02, int(self.uptime_ms)))
            if self.t_wait_pc is not None and \
                    (self.uptime_ms - self.t_wait_pc) >= self.cfg.wait_pc_timeout_ms:
                self.t_wait_pc = None
                self.error_text = "PC khong tra loi CUP_OK"
                self.tx(P.Msg.ERROR, P.encode_error(1, "PC_khong_tra_loi_CUP_OK"))
                if self.cfg.allow_manual_when_link_lost:
                    self._to(P.EspState.MANUAL)
                    self.log(" chuyen che do du phong: nut bam van dung duoc")
                else:
                    self._to(P.EspState.FAULT)

    # ---- bơm ----------------------------------------------------------
    def _update_pump(self, dt: float) -> None:
        # xung có thời hạn (PUMP_SET duration_ms) hết hạn -> tắt
        if self.pulse_end_ms is not None and self.uptime_ms >= self.pulse_end_ms:
            self.pulse_end_ms = None
            self.set_duty(0.0)
        if not self.relay_on:
            return

        # băm relay chậm theo duty (chỉ ở chế độ PC điều khiển)
        if self.fill_mode == P.FillMode.PC_CLOSED_LOOP and 0 < self.duty_pct < 100:
            cycle = max(self.cfg.duty_cycle_ms, 100)
            phase = (self.uptime_ms % cycle) / cycle
            self.relay_on = phase < (self.duty_pct / 100.0)
        else:
            self.relay_on = True

        effective = (self.duty_pct / 100.0) if self.fill_mode == P.FillMode.PC_CLOSED_LOOP else 1.0
        self.poured_ml += self.cfg.flow_ml_s * dt * effective
        self._event("poured", ml=round(self.poured_ml, 2))

        # --- an toàn ---
        if self.poured_ml > self.max_ml * 1.05:
            self._stop_pour(P.StopReason.OVER_VOLUME, " QUA THE TICH AN TOAN -> NGAT BOM")
            return
        if (self.uptime_ms - self.t_fill_start) > self.cfg.pour_timeout_ms:
            self._stop_pour(P.StopReason.TIMEOUT, " QUA THOI GIAN BOM -> NGAT")
            return

        # --- kết thúc lượt rót ---
        if self.fill_mode == P.FillMode.ESP_OPEN_LOOP:
            if self.poured_ml >= (self.target_ml - self.cfg.in_flight_ml):
                self.poured_ml += self.cfg.in_flight_ml     # nước còn trên ống
                self._stop_pour(P.StopReason.NORMAL, "")
        else:
            if self.t_zero_duty_start is not None and \
                    (self.uptime_ms - self.t_zero_duty_start) >= self.cfg.finish_zero_duty_ms:
                self._stop_pour(P.StopReason.NORMAL, " PC ket thuc (duty=0)")

    # ---- liên lạc -----------------------------------------------------
    def _update_link(self) -> None:
        if (self.uptime_ms - self.t_last_rx) <= self.cfg.link_timeout_ms:
            return
        if self.t_last_rx < 0 and self.uptime_ms <= self.cfg.link_timeout_ms:
            return
        if self._pump_running() and self.cfg.link_stops_pump:
            self._stop_pour(P.StopReason.LINK_LOST, " MAT LIEN LAC PC -> NGAT BOM")
        if self.state == P.EspState.POURING:
            self._to(P.EspState.READY)
        if self.cfg.allow_manual_when_link_lost and self.cup_sensor and \
                self.state in (P.EspState.WAIT_PC, P.EspState.READY):
            self._to(P.EspState.MANUAL)
