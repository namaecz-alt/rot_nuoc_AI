"""GIAO THỨC UART GIỮA ESP32 (bo điều khiển) VÀ MÁY TÍNH (PC nhận diện).

File này là **bản Python** của đặc tả trong ``docs/PROTOCOL.md``. Bản C++ nằm ở
``firmware/esp32_cup_filler/protocol.h`` - hai bên phải khớp byte tuyệt đối
(``tools/test_comms.py --with-cpp`` kiểm tra tự động bằng vector vàng).

Khung truyền (không dùng ký tự đặc biệt, an toàn trên UART nhị phân):

    +--------+--------+--------+--------+--------+---------...---------+--------+--------+
    | 0xAA   | 0x55   |  LEN   |  SEQ   |  MSG   |     PAYLOAD          | CRC16  | CRC16  |
    +--------+--------+--------+--------+--------+---------...---------+--------+--------+
       sync1    sync2   1 byte   1 byte   1 byte    LEN-2 byte (0..240)  thấp     cao

  * ``LEN``   = số byte kể từ ``SEQ`` tới hết payload = ``2 + len(payload)``.
  * ``CRC16`` = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, không reflect) tính trên
    các byte ``LEN, SEQ, MSG, PAYLOAD``; gửi little-endian (byte thấp trước).
  * Khung hỏng (CRC sai, LEN vô lý) bị **bỏ qua im lặng**; bộ giải mã tự tìm lại sync
    nên nhiễu trên dây không làm chết kết nối.

Luồng chính (xem ``docs/PROTOCOL.md`` để có sơ đồ đầy đủ):

    ESP32                                   MÁY TÍNH (PC)
    ----------------------------------------------------------------
    đặt cốc -> CUP_PLACED  ───────────────►  bật camera + nhận diện cốc
                            ◄─────────────── CUP_OK (hình học cốc, thể tích tối đa)
    (từ đây mới mở khoá nút bấm + mic)
    nhấn nút / nói mic -> PRESET_SELECTED ─► (PC hiển thị/log)
    relay ON  -> FILL_STARTED ────────────►  PC chạy vòng kín thị giác nếu mode=PC
               FILL_PROGRESS (5 Hz) ──────► hiển thị tiến độ
    relay OFF -> FILL_DONE ───────────────►  PC tổng kết, có thể ngắt camera
    nhấc cốc  -> CUP_REMOVED ─────────────►  PC dừng/đóng camera
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, List, Optional, Tuple

__all__ = [
    "Msg",
    "Frame",
    "FrameDecoder",
    "encode",
    "crc16_ccitt",
    "MODE_PC_CONTROLS_PUMP",
    "MODE_VOICE_STREAM",
    "MODE_LINK_STOP_PUMP",
    "FL_CUP_PRESENT",
    "FL_PC_CONFIRMED",
    "FL_PUMPING",
    "FL_VOICE_ACTIVE",
    "FL_LINK_OK",
    "FL_MANUAL",
    "AU_START",
    "AU_END",
    "PROTO_VERSION",
    "MAX_PAYLOAD",
    "CUP_OK_LABEL_LEN",
    "encode_hello",
    "parse_hello",
    "encode_cup_placed",
    "parse_cup_placed",
    "encode_cup_removed",
    "parse_cup_removed",
    "encode_preset_selected",
    "parse_preset_selected",
    "encode_fill_started",
    "parse_fill_started",
    "encode_fill_progress",
    "parse_fill_progress",
    "encode_fill_done",
    "parse_fill_done",
    "encode_status",
    "parse_status",
    "encode_voice_event",
    "parse_voice_event",
    "encode_audio_chunk",
    "parse_audio_chunk",
    "encode_button_event",
    "parse_button_event",
    "encode_hello_ack",
    "parse_hello_ack",
    "encode_cup_ok",
    "parse_cup_ok",
    "encode_cup_reject",
    "parse_cup_reject",
    "encode_set_preset",
    "parse_set_preset",
    "encode_start_fill",
    "parse_start_fill",
    "encode_stop_fill",
    "parse_stop_fill",
    "encode_set_params",
    "parse_set_params",
    "encode_pump_set",
    "parse_pump_set",
    "encode_cmd",
    "parse_cmd",
    "encode_ack",
    "parse_ack",
    "encode_ping",
    "parse_ping",
    "encode_pong",
    "parse_pong",
    "encode_error",
    "parse_error",
]

# ---------------------------------------------------------------------------
PROTO_VERSION = 1
SYNC1 = 0xAA
SYNC2 = 0x55
MAX_PAYLOAD = 240
CUP_OK_LABEL_LEN = 16


class Msg(IntEnum):
    """Mã gói tin. Bit cao = 1 -> gói do PC gửi xuống ESP32."""

    # ---- ESP32 -> PC (0x0x) ----
    HELLO = 0x01            # chào khi khởi động: phiên bản, preset, năng lực
    CUP_PLACED = 0x02       # CẢM BIẾN thấy cốc vừa được đặt vào
    CUP_REMOVED = 0x03      # cốc đã được nhấc ra
    PRESET_SELECTED = 0x04  # người dùng chọn mức bằng nút/mic (chưa chắc đã bơm)
    FILL_STARTED = 0x05     # relay bật - bắt đầu bơm
    FILL_PROGRESS = 0x06    # tiến độ (5 Hz trong lúc bơm)
    FILL_DONE = 0x07        # relay tắt - kết thúc lần bơm
    STATUS = 0x08           # nhịp tim + trạng thái (2 Hz hoặc khi đổi trạng thái)
    VOICE_EVENT = 0x09      # sự kiện giọng nói (kết quả nhận dạng trên ESP)
    AUDIO_CHUNK = 0x0A      # PCM 16-bit gửi lên PC để nhận dạng (voice_mode=stream)
    BUTTON_EVENT = 0x0B     # nhấn nút (kể cả nhấn giữ / nhấn dừng khẩn)
    ERROR = 0x0C            # lỗi của ESP32
    ACK = 0x0D              # xác nhận đã nhận gói của PC
    PONG = 0x0E             # trả lời PING kèm uptime
    LOG = 0x0F              # dòng log dạng text (debug)

    # ---- PC -> ESP32 (0x8x) ----
    HELLO_ACK = 0x81        # PC trả lời HELLO
    CUP_OK = 0x82           # ĐÃ NHẬN DIỆN XONG CỐC -> mở khoá nút/mic
    CUP_REJECT = 0x83       # không nhận diện được -> ESP báo lỗi, chờ đặt lại
    SET_PRESET = 0x84       # PC chọn hộ mức nước
    START_FILL = 0x85       # PC ra lệnh bơm (mode=0 ESP tự đong, mode=1 PC vòng kín)
    STOP_FILL = 0x86        # dừng bơm ngay
    SET_MODE = 0x87         # bật/tắt tính năng (mảng flags)
    PUMP_SET = 0x88         # PC điều khiển trực tiếp bơm (duty % + thời gian)
    SET_PARAMS = 0x89       # hiệu chuẩn: lưu lượng ml/s, thể tích tối đa, timeout
    CMD = 0x8A              # lệnh rời: arm/disarm/tare/reset...
    PING = 0x8B             # hỏi thăm (ESP trả PONG)
    ACK_PC = 0x8C           # PC xác nhận gói của ESP
    ERROR_PC = 0x8D         # PC báo lỗi cho ESP (hiện đèn/log)


# ---- mã hoá trạng thái / lý do -------------------------------------------
class EspState(IntEnum):
    BOOT = 0
    IDLE = 1          # chưa có cốc
    WAIT_PC = 2       # có cốc, đang chờ PC nhận diện (nút/mic KHOÁ)
    READY = 3         # PC đã xác nhận: nút/mic MỞ KHOÁ
    POURING = 4       # relay đang bật
    DONE = 5          # vừa bơm xong, cốc vẫn còn trên khay
    FAULT = 6
    MANUAL = 7        # PC mất kết nối nhưng vẫn cho bấm nút (tuỳ cấu hình)


STATE_NAMES = {
    EspState.BOOT: "BOOT",
    EspState.IDLE: "IDLE",
    EspState.WAIT_PC: "WAIT_PC",
    EspState.READY: "READY",
    EspState.POURING: "POURING",
    EspState.DONE: "DONE",
    EspState.FAULT: "FAULT",
    EspState.MANUAL: "MANUAL",
}


class FillMode(IntEnum):
    ESP_OPEN_LOOP = 0   # ESP tự đong theo ml/s đã hiệu chuẩn (không cần camera)
    PC_CLOSED_LOOP = 1  # PC nhìn vạch nước rồi điều khiển relay qua PUMP_SET


class Source(IntEnum):
    BUTTON = 0
    VOICE = 1
    PC = 2
    AUTO = 3


class RejectReason(IntEnum):
    NOT_FOUND = 0
    NOT_A_CUP = 1
    TOO_SMALL = 2
    BUSY = 3
    CAMERA_ERROR = 4
    UNKNOWN = 5


class StopReason(IntEnum):
    NORMAL = 0
    PC_REQUEST = 1
    CUP_REMOVED = 2
    TIMEOUT = 3
    LINK_LOST = 4
    BUTTON_ESTOP = 5
    OVER_VOLUME = 6
    SENSOR_FAULT = 7


class Cmd(IntEnum):
    ARM = 0
    DISARM = 1
    TARE = 2          # hiệu chuẩn lại mặt khay (cảm biến siêu âm) khi khay trống
    RESET_STATE = 3
    SELF_TEST = 4     # bật relay 300 ms rồi tắt (kiểm tra bơm/relay)
    SAVE_CAL = 5      # lưu lưu lượng ml/s + tham số vào NVS


# ---- cờ cho gói SET_MODE (PC bật/tắt tính năng trên ESP32) -----------------
MODE_PC_CONTROLS_PUMP = 1 << 0   # nút/mic chỉ BÁO mức nước, PC ra lệnh bơm
MODE_VOICE_STREAM = 1 << 1       # mic gửi PCM thô lên PC để nhận dạng
MODE_LINK_STOP_PUMP = 1 << 2     # mất liên lạc -> ngắt bơm (mặc định bật)


# ---- cờ trong gói STATUS (trường flags) -----------------------------------
FL_CUP_PRESENT = 1 << 0    # cảm biến đang thấy cốc
FL_PC_CONFIRMED = 1 << 1   # PC đã nhận diện và xác nhận cốc (nút/mic đã mở khoá)
FL_PUMPING = 1 << 2        # relay đang bật (bơm đang chạy)
FL_VOICE_ACTIVE = 1 << 3   # mic đang nghe
FL_LINK_OK = 1 << 4        # ESP còn nhận được gói từ PC
FL_MANUAL = 1 << 5         # đang ở chế độ dự phòng (PC mất kết nối)


# ---- cờ năng lực ----------------------------------------------------------
CAP_VISION = 1 << 0        # PC có bộ nhận diện cốc
CAP_VOICE_ASR = 1 << 1     # PC có nhận dạng tiếng nói
CAP_PC_CLOSED_LOOP = 1 << 2  # PC điều khiển bơm bằng vòng kín thị giác
CAP_CUP_INFO = 1 << 3      # PC gửi được hình học/thể tích cốc

ESP_CAP_BUTTONS = 1 << 0
ESP_CAP_VOICE = 1 << 1
ESP_CAP_US_SENSOR = 1 << 2
ESP_CAP_FLOW_CAL = 1 << 3

# ---- sự kiện nút bấm ------------------------------------------------------
BTN_SHORT = 0
BTN_LONG = 1
BTN_ESTOP = 2

# ---- loại sự kiện giọng nói ----------------------------------------------
VOICE_UNKNOWN = 0
VOICE_PRESET_WORD = 1   # nghe được "một/hai/ba..." (hoặc số tiếng)
VOICE_CLAPS = 2         # đếm số tiếng vỗ tay/gõ
VOICE_TEXT = 3          # PC nhận dạng ra chữ (ASR) - chỉ PC->ESP qua PRESET_SELECTED

# ---- cờ gói AUDIO_CHUNK ---------------------------------------------------
# AUDIO_CHUNK.flags: 4 bit cao = tần số lấy mẫu THỰC TẾ của ESP (đơn vị 2 kHz).
# Mic analog đọc bằng ADC ESP32 không đúng 16 kHz, nên firmware gửi kèm mã này để
# PC đếm "tiếng"/nhận dạng với đúng tần số. 0 = không gửi (PC dùng tần số cấu hình).
AU_RATE_SHIFT = 4
AU_RATE_STEP_HZ = 2000

AU_START = 1 << 0        # chunk đầu của một đoạn tiếng nói
AU_END = 1 << 1          # chunk cuối của đoạn (PC chốt kết quả sau chunk này)


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, không reflect, không xorout."""
    for byte in data:
        crc ^= (byte & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


# ---------------------------------------------------------------------------
@dataclass
class Frame:
    msg: int
    payload: bytes = b""
    seq: int = 0

    @property
    def name(self) -> str:
        try:
            return Msg(self.msg).name
        except ValueError:
            return "MSG_0x%02X" % self.msg

    def __repr__(self) -> str:  # pragma: no cover - chỉ để debug
        return "Frame(%s, seq=%d, len=%d)" % (self.name, self.seq, len(self.payload))


def encode(msg: int, payload: bytes = b"", seq: int = 0) -> bytes:
    """Đóng gói một frame hoàn chỉnh để gửi qua UART."""
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload quá dài: %d > %d" % (len(payload), MAX_PAYLOAD))
    body = struct.pack("<BBB", 2 + len(payload), seq & 0xFF, msg & 0xFF) + payload
    crc = crc16_ccitt(body)
    return bytes((SYNC1, SYNC2)) + body + struct.pack("<H", crc)


class FrameDecoder:
    """Bộ giải mã streaming: nạp byte vào, nhận về danh sách Frame hợp lệ.

    Tự đồng bộ lại sau nhiễu: khi CRC sai hoặc LEN vô lý, bộ đệm bị trượt đi một
    byte và quá trình dò sync tiếp tục, nên một gói hỏng không làm mất các gói sau.
    """

    def __init__(self, max_payload: int = MAX_PAYLOAD, max_buffer: int = 4096):
        self.max_payload = max_payload
        self.max_buffer = max_buffer
        self._buf = bytearray()
        self.n_frames = 0
        self.n_bad_crc = 0
        self.n_bad_len = 0
        self.n_resync = 0
        self.n_dropped_bytes = 0

    def reset(self) -> None:
        self._buf.clear()

    # -- nạp dữ liệu ----------------------------------------------------
    def feed(self, data: bytes, out: Optional[List[Frame]] = None) -> List[Frame]:
        """Nạp thêm byte rồi giải mã ngay (theo từng khối 4 kB để không mất dữ liệu
        khi một lần nạp rất lớn - ví dụ hàng nghìn mẫu audio gửi lên cùng lúc)."""
        frames: List[Frame] = out if out is not None else []
        if data:
            step = 4096
            for i in range(0, len(data), step):
                self._buf.extend(data[i : i + step])
                self._parse(frames)
        if len(self._buf) > self.max_buffer:
            # an toàn bộ nhớ: sau khi giải mã, phần dư chỉ nên là một gói dở;
            # vượt xa mức đó nghĩa là rác không có sync -> cắt bớt
            drop = len(self._buf) - self.max_buffer
            del self._buf[:drop]
            self.n_dropped_bytes += drop
        return frames

    def _parse(self, frames: List[Frame]) -> None:
        buf = self._buf
        while True:
            # 1. dò sync
            i = buf.find(b"\xaa\x55")
            if i < 0:
                # giữ lại tối đa 1 byte cuối (có thể là 0xAA nửa đầu của sync)
                keep = 1 if buf and buf[-1] == SYNC1 else 0
                self.n_dropped_bytes += len(buf) - keep
                if keep:
                    del buf[: len(buf) - keep]
                else:
                    buf.clear()
                return
            if i > 0:
                self.n_resync += 1
                self.n_dropped_bytes += i
                del buf[:i]
            if len(buf) < 4:
                return
            length = buf[2]
            if length < 2 or length > 2 + self.max_payload:
                # LEN vô lý -> bỏ 1 byte sync đầu rồi dò lại
                self.n_bad_len += 1
                del buf[:1]
                continue
            # bố cục: [0]=AA [1]=55 [2]=LEN [3]=SEQ [4]=MSG [5..]=payload
            # LEN = SEQ + MSG + payload, nên "body" (LEN..payload) dài LEN+1 byte
            total = 5 + length          # sync(2) + LEN(1) + LEN byte + CRC(2)
            if len(buf) < total:
                return
            body = bytes(buf[2 : 3 + length])
            crc_rx = buf[3 + length] | (buf[4 + length] << 8)
            if crc16_ccitt(body) != crc_rx:
                self.n_bad_crc += 1
                del buf[:1]            # trượt 1 byte để không mất gói kế tiếp
                continue
            frames.append(Frame(msg=body[2], seq=body[1], payload=body[3:]))
            self.n_frames += 1
            del buf[:total]

    @property
    def stats(self) -> dict:
        return {
            "frames": self.n_frames,
            "bad_crc": self.n_bad_crc,
            "bad_len": self.n_bad_len,
            "resync": self.n_resync,
            "dropped_bytes": self.n_dropped_bytes,
        }


# ---------------------------------------------------------------------------
#  Bộ mã hoá / giải mã payload từng loại gói
# ---------------------------------------------------------------------------
def _text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def _bytes(text: str, n: int) -> bytes:
    raw = (text or "")[: n - 1].encode("utf-8")
    return raw + b"\x00" * (n - len(raw))


# ---- ESP32 -> PC ----------------------------------------------------------
def encode_hello(version: int, caps: int, presets: Iterable[int], flow_ml_s: float = 0.0) -> bytes:
    """HELLO: phiên bản + năng lực + danh sách preset + lưu lượng bơm đã hiệu chuẩn."""
    presets = list(presets)[:8]
    flow = int(round(float(flow_ml_s) * 10)) & 0xFFFF   # gửi dạng 0.1 ml/s
    return struct.pack("<BBBBH", version & 0xFF, caps & 0xFF, len(presets), 0, flow) + \
        b"".join(struct.pack("<H", int(p) & 0xFFFF) for p in presets)


def parse_hello(payload: bytes) -> dict:
    ver, caps, n, _pad, flow = struct.unpack_from("<BBBBH", payload, 0)
    presets = list(struct.unpack_from("<%dH" % n, payload, 6)) if n else []
    return {"version": ver, "caps": caps, "presets": presets, "flow_ml_s": flow / 10.0}


def encode_cup_placed(distance_mm: int, baseline_mm: int, height_mm: int, flags: int, uptime_ms: int) -> bytes:
    return struct.pack("<HHHBI", int(distance_mm) & 0xFFFF, int(baseline_mm) & 0xFFFF,
                       int(height_mm) & 0xFFFF, int(flags) & 0xFF, int(uptime_ms) & 0xFFFFFFFF)


def parse_cup_placed(payload: bytes) -> dict:
    d, b, h, f, t = struct.unpack_from("<HHHBI", payload, 0)
    return {"distance_mm": d, "baseline_mm": b, "height_mm": h, "flags": f, "uptime_ms": t}


def encode_cup_removed(reason: int, uptime_ms: int) -> bytes:
    return struct.pack("<BI", reason, uptime_ms)


def parse_cup_removed(payload: bytes) -> dict:
    r, t = struct.unpack_from("<BI", payload, 0)
    return {"reason": r, "uptime_ms": t}


def encode_preset_selected(index: int, ml: int, source: int) -> bytes:
    return struct.pack("<BHB", index, ml, source)


def parse_preset_selected(payload: bytes) -> dict:
    i, ml, s = struct.unpack_from("<BHB", payload, 0)
    return {"index": i, "ml": ml, "source": s}


def encode_fill_started(ml: int, mode: int, source: int, max_ml: int) -> bytes:
    return struct.pack("<HBBH", ml, mode, source, max_ml)


def parse_fill_started(payload: bytes) -> dict:
    ml, mode, src, mx = struct.unpack_from("<HBBH", payload, 0)
    return {"ml": ml, "mode": mode, "source": src, "max_ml": mx}


def encode_fill_progress(poured_ml: int, pct: int, target_ml: int, state: int) -> bytes:
    return struct.pack("<HBHB", poured_ml, pct, target_ml, state)


def parse_fill_progress(payload: bytes) -> dict:
    p, pct, t, s = struct.unpack_from("<HBHB", payload, 0)
    return {"poured_ml": p, "pct": pct, "target_ml": t, "state": s}


def encode_fill_done(poured_ml: int, target_ml: int, status: int, elapsed_ms: int) -> bytes:
    return struct.pack("<HHBI", poured_ml, target_ml, status, elapsed_ms)


def parse_fill_done(payload: bytes) -> dict:
    p, t, s, e = struct.unpack_from("<HHBI", payload, 0)
    return {"poured_ml": p, "target_ml": t, "status": s, "elapsed_ms": e}


def encode_status(state: int, flags: int, poured_ml: int, target_ml: int, max_ml: int, uptime_ms: int) -> bytes:
    return struct.pack("<BBHHHI", state, flags, poured_ml, target_ml, max_ml, uptime_ms)


def parse_status(payload: bytes) -> dict:
    st, fl, p, t, mx, up = struct.unpack_from("<BBHHHI", payload, 0)
    return {
        "state": st,
        "state_name": STATE_NAMES.get(_state_or_none(st), "0x%02X" % st),
        "flags": fl,
        "cup_present": bool(fl & FL_CUP_PRESENT),
        "pc_confirmed": bool(fl & FL_PC_CONFIRMED),
        "pumping": bool(fl & FL_PUMPING),
        "voice_active": bool(fl & FL_VOICE_ACTIVE),
        "link_ok": bool(fl & FL_LINK_OK),
        "poured_ml": p,
        "target_ml": t,
        "max_ml": mx,
        "uptime_ms": up,
    }


def _state_or_none(value: int):
    try:
        return EspState(value)
    except ValueError:
        return None


def encode_voice_event(kind: int, index: int, confidence: int, n_peaks: int, rms: int, dur_ms: int) -> bytes:
    return struct.pack("<BBBBHH", kind, index, confidence, n_peaks, rms, dur_ms)


def parse_voice_event(payload: bytes) -> dict:
    k, i, c, n, rms, d = struct.unpack_from("<BBBBHH", payload, 0)
    return {"kind": k, "index": i, "confidence": c, "n_peaks": n, "rms": rms, "dur_ms": d}


def encode_audio_chunk(seq8: int, flags: int, samples: Iterable[int],
                       rate_hz: int = 0) -> bytes:
    """Đóng gói PCM. ``rate_hz`` > 0 sẽ ghi mã tần số thực tế vào 4 bit cao của flags."""
    samples = list(samples)
    if rate_hz:
        flags = (int(flags) & 0x0F) | (((int(round(rate_hz / AU_RATE_STEP_HZ))) & 0x0F)
                                       << AU_RATE_SHIFT)
    head = struct.pack("<BBH", seq8 & 0xFF, flags & 0xFF, len(samples))
    return head + struct.pack("<%dh" % len(samples), *samples) if samples else head


def parse_audio_chunk(payload: bytes) -> dict:
    seq8, flags, n = struct.unpack_from("<BBH", payload, 0)
    samples = list(struct.unpack_from("<%dh" % n, payload, 4)) if n else []
    rate_hz = ((flags >> AU_RATE_SHIFT) & 0x0F) * AU_RATE_STEP_HZ
    return {"seq": seq8, "flags": flags, "start": bool(flags & AU_START), "end": bool(flags & AU_END),
            "rate_hz": rate_hz, "n_samples": n, "samples": samples}


def encode_button_event(index: int, event: int, press_ms: int, state: int) -> bytes:
    return struct.pack("<BHBB", index, press_ms, event, state)


def parse_button_event(payload: bytes) -> dict:
    i, ms, ev, st = struct.unpack_from("<BHBB", payload, 0)
    return {"index": i, "press_ms": ms, "event": ev, "state": st}


def encode_error(code: int, text: str = "") -> bytes:
    return struct.pack("<B", code & 0xFF) + _bytes(text, 48)


def parse_error(payload: bytes) -> dict:
    code = payload[0] if payload else 0
    return {"code": code, "text": _text(payload[1:])}


def encode_ack(acked_msg: int, status: int = 0) -> bytes:
    return struct.pack("<BB", acked_msg & 0xFF, status & 0xFF)


def parse_ack(payload: bytes) -> dict:
    m, s = struct.unpack_from("<BB", payload, 0)
    return {"acked_msg": m, "status": s}


# ---- PC -> ESP32 ----------------------------------------------------------
def encode_hello_ack(version: int, caps: int, presets: Iterable[int]) -> bytes:
    presets = list(presets)[:8]
    return struct.pack("<BBB", version & 0xFF, caps & 0xFF, len(presets)) + \
        b"".join(struct.pack("<H", int(p) & 0xFFFF) for p in presets)


def parse_hello_ack(payload: bytes) -> dict:
    ver, caps, n = struct.unpack_from("<BBB", payload, 0)
    presets = list(struct.unpack_from("<%dH" % n, payload, 3)) if n else []
    return {"version": ver, "caps": caps, "presets": presets}


def encode_cup_ok(rim_r_mm: float, base_r_mm: float, height_mm: float,
                  max_ml: float, confidence: float, flags: int = 0, label: str = "") -> bytes:
    body = struct.pack(
        "<HHHHBB",
        int(round(max(0.0, rim_r_mm) * 10)) & 0xFFFF,
        int(round(max(0.0, base_r_mm) * 10)) & 0xFFFF,
        int(round(max(0.0, height_mm) * 10)) & 0xFFFF,
        int(round(max(0.0, max_ml))) & 0xFFFF,
        int(round(max(0.0, min(1.0, confidence)) * 100)) & 0xFF,
        flags & 0xFF,
    )
    return body + _bytes(label, CUP_OK_LABEL_LEN)


def parse_cup_ok(payload: bytes) -> dict:
    rim, base, h, mx, conf, flags = struct.unpack_from("<HHHHBB", payload, 0)
    label = _text(payload[10:10 + CUP_OK_LABEL_LEN])   # 10 byte đầu là <HHHHBB>
    return {
        "rim_r_mm": rim / 10.0, "base_r_mm": base / 10.0, "height_mm": h / 10.0,
        "max_ml": mx, "confidence": conf / 100.0, "flags": flags, "label": label,
    }


def encode_cup_reject(reason: int, text: str = "") -> bytes:
    return struct.pack("<B", reason & 0xFF) + _bytes(text, 48)


def parse_cup_reject(payload: bytes) -> dict:
    return {"reason": payload[0] if payload else 0, "text": _text(payload[1:])}


def encode_set_preset(index: int, ml: int, source: int = Source.PC) -> bytes:
    return struct.pack("<BHB", index & 0xFF, int(ml) & 0xFFFF, int(source) & 0xFF)


def parse_set_preset(payload: bytes) -> dict:
    i, ml, s = struct.unpack_from("<BHB", payload, 0)
    return {"index": i, "ml": ml, "source": s}


def encode_start_fill(ml: int, mode: int = FillMode.ESP_OPEN_LOOP,
                      source: int = Source.PC, timeout_ms: int = 0) -> bytes:
    return struct.pack("<HBBH", int(ml) & 0xFFFF, int(mode) & 0xFF, int(source) & 0xFF,
                       int(timeout_ms) & 0xFFFF)


def parse_start_fill(payload: bytes) -> dict:
    ml, mode, src, t = struct.unpack_from("<HBBH", payload, 0)
    return {"ml": ml, "mode": mode, "source": src, "timeout_ms": t}


def encode_stop_fill(reason: int = StopReason.PC_REQUEST) -> bytes:
    return struct.pack("<B", int(reason) & 0xFF)


def parse_stop_fill(payload: bytes) -> dict:
    return {"reason": payload[0] if payload else 0}


def encode_set_mode(flags_on: int = 0, flags_off: int = 0) -> bytes:
    return struct.pack("<HH", flags_on & 0xFFFF, flags_off & 0xFFFF)


def parse_set_mode(payload: bytes) -> dict:
    on, off = struct.unpack_from("<HH", payload, 0)
    return {"on": on, "off": off}


def encode_pump_set(duty_pct: float, duration_ms: int = 0) -> bytes:
    return struct.pack("<BH", int(round(max(0.0, min(1.0, duty_pct)) * 100)) & 0xFF,
                       int(round(duration_ms)) & 0xFFFF)


def parse_pump_set(payload: bytes) -> dict:
    d, t = struct.unpack_from("<BH", payload, 0)
    return {"duty": d / 100.0, "duration_ms": t}


def encode_set_params(flow_ml_s: float = None, max_ml: int = None, timeout_s: int = None,
                      pulse_ms: int = None) -> bytes:
    """Gửi tham số hiệu chuẩn; trường None = giữ nguyên giá trị trên ESP."""
    def u16(v):
        return 0xFFFF if v is None else int(v) & 0xFFFF

    return struct.pack("<HHHH", u16(None if flow_ml_s is None else flow_ml_s * 10),
                       u16(max_ml), u16(timeout_s), u16(pulse_ms))


def parse_set_params(payload: bytes) -> dict:
    flow, mx, to, pulse = struct.unpack_from("<HHHH", payload, 0)
    return {
        "flow_ml_s": None if flow == 0xFFFF else flow / 10.0,
        "max_ml": None if mx == 0xFFFF else mx,
        "timeout_s": None if to == 0xFFFF else to,
        "pulse_ms": None if pulse == 0xFFFF else pulse,
    }


def encode_cmd(cmd: int, arg: int = 0) -> bytes:
    return struct.pack("<BH", int(cmd) & 0xFF, int(arg) & 0xFFFF)


def parse_cmd(payload: bytes) -> dict:
    c, a = struct.unpack_from("<BH", payload, 0)
    return {"cmd": c, "arg": a}


def encode_ping(uptime_ms: int = 0) -> bytes:
    return struct.pack("<I", int(uptime_ms) & 0xFFFFFFFF)


def parse_ping(payload: bytes) -> dict:
    up, = struct.unpack_from("<I", payload, 0)
    return {"uptime_ms": up}


def encode_pong(uptime_ms: int, state: int = 0, flags: int = 0) -> bytes:
    return struct.pack("<IBB", int(uptime_ms) & 0xFFFFFFFF, state & 0xFF, flags & 0xFF)


def parse_pong(payload: bytes) -> dict:
    up, st, fl = struct.unpack_from("<IBB", payload, 0)
    return {"uptime_ms": up, "state": st, "flags": fl}


# ---------------------------------------------------------------------------
PARSERS = {
    Msg.HELLO: parse_hello,
    Msg.CUP_PLACED: parse_cup_placed,
    Msg.CUP_REMOVED: parse_cup_removed,
    Msg.PRESET_SELECTED: parse_preset_selected,
    Msg.FILL_STARTED: parse_fill_started,
    Msg.FILL_PROGRESS: parse_fill_progress,
    Msg.FILL_DONE: parse_fill_done,
    Msg.STATUS: parse_status,
    Msg.VOICE_EVENT: parse_voice_event,
    Msg.AUDIO_CHUNK: parse_audio_chunk,
    Msg.BUTTON_EVENT: parse_button_event,
    Msg.ERROR: parse_error,
    Msg.ACK: parse_ack,
    Msg.PONG: parse_pong,
    Msg.LOG: lambda p: {"text": p.decode("utf-8", "replace")},
    Msg.HELLO_ACK: parse_hello_ack,
    Msg.CUP_OK: parse_cup_ok,
    Msg.CUP_REJECT: parse_cup_reject,
    Msg.SET_PRESET: parse_set_preset,
    Msg.START_FILL: parse_start_fill,
    Msg.STOP_FILL: parse_stop_fill,
    Msg.SET_MODE: parse_set_mode,
    Msg.PUMP_SET: parse_pump_set,
    Msg.SET_PARAMS: parse_set_params,
    Msg.CMD: parse_cmd,
    Msg.PING: parse_ping,
    Msg.ACK_PC: parse_ack,
    Msg.ERROR_PC: parse_error,
}


def decode(frame: Frame) -> dict:
    """Giải payload thành dict. Gói lạ -> {'raw': bytes}."""
    fn = PARSERS.get(frame.msg) if isinstance(frame.msg, int) else None
    if fn is None:
        try:
            fn = PARSERS.get(Msg(frame.msg))
        except ValueError:
            fn = None
    if fn is None:
        return {"raw": frame.payload}
    try:
        return fn(frame.payload)
    except struct.error as exc:  # payload ngắn/hỏng -> không làm chết vòng lặp
        return {"error": "payload ngắn: %s" % exc, "raw": frame.payload}
