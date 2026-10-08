"""Tầng truyền UART giữa PC và ESP32: khung gói, hàng đợi sự kiện, thống kê lỗi.

Hỗ trợ 4 kiểu "cổng" (đều dùng chung một API):

    ``COM5`` / ``/dev/ttyUSB0``   -> cổng UART thật (pyserial)
    ``tcp://127.0.0.1:5555``      -> nối tới ``tools/esp_sim.py`` (thử không cần phần cứng)
    ``sim``                       -> chạy luôn bộ giả lập ESP32 trong tiến trình này
    ``loopback``                  -> cặp đệm trong RAM (dùng cho unit test)

Cách dùng điển hình::

    from cupfiller.serial_link import open_link
    link = open_link("COM5", baud=921600)
    link.send(P.Msg.PING, P.encode_ping(0))
    for frame, fields in link.poll():
        print(P.Msg(frame.msg).name, fields)

Mọi gói gửi đi đều được đánh số ``seq`` tăng dần (dùng để đối chiếu ACK), mọi gói
nhận vào đều qua kiểm tra CRC; số gói hỏng được đếm trong ``link.stats``.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import protocol as P

__all__ = ["LinkBase", "SerialPortLink", "TcpLink", "LoopbackLink", "SerialEsp", "open_link"]


class LinkBase:
    """Cổng truyền thô: chỉ cần write()/read()/close()."""

    name = "link"

    def write(self, data: bytes) -> int:  # pragma: no cover - lớp cơ sở
        raise NotImplementedError

    def read(self, max_bytes: int = 4096) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def is_open(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name


class LoopbackLink(LinkBase):
    """Cặp đệm trong RAM: hai đầu nối vào nhau (unit test, giả lập trong tiến trình).

    ``a.write(data)`` -> dữ liệu nằm trong hộp thư của ``b``; ``b.read()`` lấy ra.
    """

    def __init__(self) -> None:
        self._inbox = bytearray()          # dữ liệu gửi TỚI đầu này
        self._lock = threading.Lock()
        self._open = True
        self.peer: Optional["LoopbackLink"] = None
        self.name = "loopback"

    @staticmethod
    def pair() -> Tuple["LoopbackLink", "LoopbackLink"]:
        a, b = LoopbackLink(), LoopbackLink()
        a.peer, b.peer = b, a
        return a, b

    def write(self, data: bytes) -> int:
        if not self._open or self.peer is None or not self.peer._open:
            return 0
        with self.peer._lock:
            self.peer._inbox.extend(data)
        return len(data)

    def read(self, max_bytes: int = 4096) -> bytes:
        with self._lock:
            n = min(max_bytes, len(self._inbox))
            if n <= 0:
                return b""
            out = bytes(self._inbox[:n])
            del self._inbox[:n]
            return out

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def describe(self) -> str:
        return "loopback (trong RAM)"


class SerialPortLink(LinkBase):
    """Cổng UART thật qua pyserial (ESP32 nối USB-UART CP2102/CH340)."""

    def __init__(self, port: str, baud: int = 921600, timeout: float = 0.02):
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Thiếu pyserial: chạy  pip install pyserial  (hoặc  pip install -r requirements_pc.txt)"
            ) from exc
        self.serial = serial.Serial(port=port, baudrate=int(baud), timeout=timeout,
                                    write_timeout=0.5)
        # ESP32 reset khi mở cổng (DTR/RTS) -> chờ nó khởi động lại
        try:
            self.serial.setDTR(False)
            self.serial.setRTS(False)
        except Exception:
            pass
        time.sleep(0.2)
        try:
            self.serial.reset_input_buffer()
        except Exception:
            pass
        self.port = port
        self.baud = int(baud)
        self.name = "%s@%d" % (port, baud)

    def write(self, data: bytes) -> int:
        return int(self.serial.write(data))

    def read(self, max_bytes: int = 4096) -> bytes:
        try:
            waiting = self.serial.in_waiting
        except Exception:
            waiting = 0
        n = min(max_bytes, max(waiting, 1))
        return bytes(self.serial.read(n))

    def close(self) -> None:
        try:
            self.serial.close()
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        return bool(getattr(self.serial, "is_open", False))

    def describe(self) -> str:
        return "UART %s @ %d baud" % (self.port, self.baud)


class TcpLink(LinkBase):
    """Nối qua TCP tới bộ giả lập ESP32 (tools/esp_sim.py) - dùng khi chưa có bo mạch."""

    def __init__(self, host: str, port: int, timeout: float = 0.02):
        import socket

        self.host, self.port = host, int(port)
        self.sock = socket.create_connection((self.host, self.port), timeout=2.0)
        self.sock.settimeout(timeout)
        self.name = "tcp://%s:%d" % (self.host, self.port)

    def write(self, data: bytes) -> int:
        try:
            self.sock.sendall(data)
            return len(data)
        except OSError:
            return 0

    def read(self, max_bytes: int = 4096) -> bytes:
        try:
            data = self.sock.recv(max_bytes)
        except OSError:
            return b""
        if not data:
            self.sock = None
            return b""
        return data

    @property
    def is_open(self) -> bool:
        return self.sock is not None

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def describe(self) -> str:
        return "TCP %s" % self.name


# ---------------------------------------------------------------------------
class SerialEsp:
    """Bọc một cổng truyền + giao thức: gửi/nhận gói, thống kê, cảnh báo mất kết nối."""

    def __init__(self, link: LinkBase, log: Optional[Callable[[str], None]] = None,
                 link_timeout_s: float = 3.0, auto_thread: bool = True):
        self.link = link
        self.decoder = P.FrameDecoder()
        self.seq = 0
        self.log = log or (lambda msg: None)
        self.link_timeout_s = float(link_timeout_s)
        self.n_tx = 0
        self.n_rx = 0
        self.t_last_rx = 0.0
        self.t_start = time.time()
        self.received: List[P.Frame] = []        # gói PC nhận được từ ESP32
        self.sent: List[P.Frame] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if auto_thread and not isinstance(link, LoopbackLink):
            self._thread = threading.Thread(target=self._rx_loop, daemon=True)
            self._thread.start()

    # ---- vòng nhận ---------------------------------------------------
    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self.link.read(4096)
            except Exception as exc:
                self.log("[link] lỗi đọc: %s" % exc)
                data = b""
            if data:
                self.feed(data)
            else:
                time.sleep(0.005)

    def feed(self, data: bytes) -> List[P.Frame]:
        """Nạp byte thô vào bộ giải mã (dùng khi không chạy luồng nền)."""
        frames = self.decoder.feed(data)
        if frames:
            with self._lock:
                self.received.extend(frames)
                self.n_rx += len(frames)
            self.t_last_rx = time.time()
        return frames

    def poll(self, max_items: int = 200) -> List[Tuple[P.Frame, dict]]:
        """Lấy toàn bộ gói đã nhận kể từ lần gọi trước, kèm dict đã giải mã."""
        if isinstance(self.link, LoopbackLink):
            data = self.link.read(65536)
            if data:
                self.feed(data)
        with self._lock:
            frames = self.received[:max_items]
            del self.received[:max_items]
        return [(f, P.decode(f)) for f in frames]

    # ---- gửi ---------------------------------------------------------
    def send(self, msg: int, payload: bytes = b"") -> P.Frame:
        self.seq = (self.seq + 1) & 0xFF
        frame = P.Frame(msg=int(msg), payload=bytes(payload), seq=self.seq)
        raw = P.encode(frame.msg, frame.payload, frame.seq)
        n = self.link.write(raw)
        if n != len(raw):
            self.log("[link] ghi thiếu byte (%d/%d)" % (n, len(raw)))
        self.n_tx += 1
        self.sent.append(frame)
        if len(self.sent) > 500:
            del self.sent[:250]
        return frame

    # ---- tiện ích gửi theo từng loại gói ------------------------------
    def send_hello_ack(self, version: int, caps: int, presets) -> None:
        self.send(P.Msg.HELLO_ACK, P.encode_hello_ack(version, caps, presets))

    def send_cup_ok(self, rim_r_mm: float, base_r_mm: float, height_mm: float,
                    max_ml: float, confidence: float, flags: int = 0, label: str = "") -> None:
        self.send(P.Msg.CUP_OK, P.encode_cup_ok(rim_r_mm, base_r_mm, height_mm,
                                                max_ml, confidence, flags, label))

    def send_cup_reject(self, reason: int = P.RejectReason.NOT_FOUND, text: str = "") -> None:
        self.send(P.Msg.CUP_REJECT, P.encode_cup_reject(reason, text))

    def send_set_preset(self, index: int, ml: int, source: int = P.Source.PC) -> None:
        self.send(P.Msg.SET_PRESET, P.encode_set_preset(index, ml, source))

    def send_start_fill(self, ml: int, mode: int = P.FillMode.ESP_OPEN_LOOP,
                        source: int = P.Source.PC, timeout_ms: int = 0) -> None:
        self.send(P.Msg.START_FILL, P.encode_start_fill(ml, mode, source, timeout_ms))

    def send_stop_fill(self, reason: int = P.StopReason.PC_REQUEST) -> None:
        self.send(P.Msg.STOP_FILL, P.encode_stop_fill(reason))

    def send_pump_set(self, duty: float, duration_ms: int = 0) -> None:
        self.send(P.Msg.PUMP_SET, P.encode_pump_set(duty, duration_ms))

    def send_set_params(self, flow_ml_s=None, max_ml=None, timeout_s=None, pulse_ms=None) -> None:
        self.send(P.Msg.SET_PARAMS, P.encode_set_params(flow_ml_s, max_ml, timeout_s, pulse_ms))

    def send_cmd(self, cmd: int, arg: int = 0) -> None:
        self.send(P.Msg.CMD, P.encode_cmd(cmd, arg))

    def send_ping(self, uptime_ms: int = 0) -> None:
        self.send(P.Msg.PING, P.encode_ping(uptime_ms))

    def send_ack(self, acked_msg: int, status: int = 0) -> None:
        self.send(P.Msg.ACK_PC, P.encode_ack(acked_msg, status))

    def send_error(self, code: int, text: str = "") -> None:
        self.send(P.Msg.ERROR_PC, P.encode_error(code, text))

    # ---- trạng thái ---------------------------------------------------
    @property
    def link_ok(self) -> bool:
        if not self.link.is_open:
            return False
        return (time.time() - self.t_last_rx) < self.link_timeout_s

    @property
    def stats(self) -> dict:
        st = dict(self.decoder.stats)
        st.update({
            "tx": self.n_tx, "rx": self.n_rx,
            "port": self.link.describe(), "link_ok": self.link_ok,
            "seconds_since_rx": round(time.time() - self.t_last_rx, 2) if self.t_last_rx else None,
        })
        return st

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.link.close()


# ---------------------------------------------------------------------------
def open_link(spec: str, baud: int = 921600, log: Optional[Callable[[str], None]] = None,
              link_timeout_s: float = 3.0, sim_kwargs: Optional[Dict] = None) -> SerialEsp:
    """Mở kết nối theo chuỗi mô tả: COM5 | /dev/ttyUSB0 | tcp://host:port | sim | loopback."""
    spec = (spec or "sim").strip()
    if spec in ("sim", "simulator", "esp_sim"):
        from .esp_sim import Esp32Simulator

        a, b = LoopbackLink.pair()
        link = SerialEsp(a, log=log, link_timeout_s=link_timeout_s, auto_thread=False)
        sim = Esp32Simulator(**(sim_kwargs or {}))
        sim.attach(b)
        link.simulator = sim            # type: ignore[attr-defined]
        return link
    if spec.startswith("tcp://"):
        body = spec[len("tcp://"):]
        host, _, port = body.rpartition(":")
        return SerialEsp(TcpLink(host or "127.0.0.1", int(port or 5555)), log=log,
                         link_timeout_s=link_timeout_s)
    if spec == "loopback":
        a, _b = LoopbackLink.pair()
        return SerialEsp(a, log=log, link_timeout_s=link_timeout_s, auto_thread=False)
    return SerialEsp(SerialPortLink(spec, baud=baud), log=log, link_timeout_s=link_timeout_s)
