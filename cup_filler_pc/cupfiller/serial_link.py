r"""GIAO THUC UART voi ESP32 - ban Python (ban chuan nam o
``firmware/esp32/uart_protocol.h``; hai ben PHAI trung khop).

Khuung tin van ban, doc duoc tren Serial Monitor::

    $TYPE[,arg1,arg2,...]*XX\n

``XX`` = 2 chu so hex (chu thuong) cua tong cac byte giua ``$`` va ``*`` (mod 256).

Bang tin dung trong du an:

    ESP32 -> MAY TINH
      BOOT,<ver>              ESP32 vua khoi dong / vua duoc may tinh goi HELLO
      HB,<ms>,<state>,<cup>,<armed>,<duty>   nhip song, gui moi 250 ms
      CUP,<0|1>               cam bien coc thay doi (da loc rung)
      BTN,<id>,<DOWN|LONG>    nut bam (id 1..5 = muc, 0 = DUNG)
      LVL,<ml>                muc rot duoc chon
      START,<ml>              nguoi dung yeu cau rot (nut hoac mic)
      STOP,<ly_do>            dung rot (BTN_STOP / CUP_REMOVED / PC_TIMEOUT / ...)
      DONE,<ms>               mot lan rot ket thuc binh thuong
      PUMP,<duty>             tieng vang muc bom ESP32 dang giu
      ERR,<ma>                loi (NO_CUP_VISION / ACK_TIMEOUT / PC_TIMEOUT / BUSY ...)
      CFG,OK                  da nhan danh sach muc rot tu may tinh

    MAY TINH -> ESP32
      HELLO,<ver>             bat tay; ESP32 tra loi BOOT + trang thai hien tai
      CFG,<ml;ml;...>         day danh sach muc rot xuong ESP32
      ACK,<0|1>               camera co thay coc khong -> cho phep / khong cho phep rot
      PUMP,<0..100>           muc bom (ESP32 bam mem ra relay, tich cuc CAO)
      STATE,<ten>             trang thai FSM may tinh (DONE/FAULT la quan trong nhat)
      HB,<ms>                 nhip song cua may tinh (ESP32 dung lam watchdog)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

__all__ = [
    "checksum",
    "encode_frame",
    "FrameParser",
    "Msg",
    "Link",
    "SerialEspLink",
    "ProcessEspLink",
    "EspChannel",
    "find_sim_binary",
]

# ---------------------------------------------------------------------------
#  MA HOA / GIAI MA
# ---------------------------------------------------------------------------
MAX_BODY = 64


def checksum(body: str) -> int:
    return sum(body.encode("ascii", "ignore")) & 0xFF


def encode_frame(ftype: str, args: Sequence = ()) -> str:
    """Tao khuung ``$TYPE,args*xx\\n``. Ky tu cam trong args se cat phan con lai."""
    if isinstance(args, (str, bytes)):
        args = (args,) if args != () and args != "" else ()
    body = ftype
    if args:
        body += "," + ",".join(str(a) for a in args)
    for bad in "$*\n\r":
        body = body.split(bad, 1)[0]
    if len(body) > MAX_BODY - 1:
        body = body[: MAX_BODY - 1]
    return "$%s*%02x\n" % (body, checksum(body))


@dataclass
class Msg:
    """Mot ban tin da giai ma."""

    type: str
    args: List[str] = field(default_factory=list)
    raw: str = ""

    def int_arg(self, i: int, default: int = 0) -> int:
        try:
            return int(self.args[i])
        except (IndexError, ValueError):
            return default

    def str_arg(self, i: int, default: str = "") -> str:
        return self.args[i] if i < len(self.args) else default

    def __str__(self) -> str:  # pragma: no cover - chi de log
        return self.raw.strip()


class FrameParser:
    """May trang thai giong het ``cupfiller::FrameParser`` trong firmware."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._st = 0          # 0=IDLE 1=BODY 2=CRC1 3=CRC2
        self._body: List[str] = []
        self._crc = 0
        self.errors = 0
        self.last: Optional[Msg] = None

    def feed(self, data: bytes) -> List[Msg]:
        """Nap mot khoi byte, tra ve danh sach ban tin hop le (co the rong)."""
        out: List[Msg] = []
        for b in data:
            c = chr(b)
            if c == "$":                      # bat dau khuung (dong bo lai)
                self._st, self._body, self._crc = 1, [], 0
                continue
            if self._st == 0:
                continue                      # rac ngoai khuung
            if self._st == 1:
                if c == "*":
                    self._st = 2
                elif c in "\r\n":
                    self._st, self.errors = 0, self.errors + 1
                elif len(self._body) + 1 >= MAX_BODY:
                    self._st, self.errors = 0, self.errors + 1
                else:
                    self._body.append(c)
                continue
            if self._st == 2:
                v = _hex_val(c)
                if v < 0:
                    self._st, self.errors = 0, self.errors + 1
                else:
                    self._crc, self._st = v << 4, 3
                continue
            if self._st == 3:
                v = _hex_val(c)
                self._st = 0
                if v < 0:
                    self.errors += 1
                    continue
                self._crc |= v
                body = "".join(self._body)
                if self._crc != checksum(body):
                    self.errors += 1
                    continue
                ftype, _, rest = body.partition(",")
                if not ftype:
                    continue
                m = Msg(ftype, rest.split(",") if rest else [], "$%s\n" % body)
                self.last = m
                out.append(m)
        return out


def _hex_val(c: str) -> int:
    if "0" <= c <= "9":
        return ord(c) - 48
    if "a" <= c <= "f":
        return ord(c) - 87
    if "A" <= c <= "F":
        return ord(c) - 55
    return -1


# ---------------------------------------------------------------------------
#  KENH TRUYEN
# ---------------------------------------------------------------------------
class Link:
    """Mot kenh byte 2 chieu (UART that, subprocess, hoac ESP32 ao trong Python)."""

    name = "link"

    def write(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, n: int = 512) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def control(self, cmd: str) -> None:
        """Dieu khien dau vao gia lap (chi co o che do mo phong)."""
        raise NotImplementedError("%s khong ho tro dieu khien gia lap" % self.name)

    @property
    def ok(self) -> bool:
        return True

    def close(self) -> None:  # pragma: no cover - interface
        pass


class SerialEspLink(Link):
    """UART that qua pyserial (COM5 / /dev/ttyUSB0 ...)."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.0):
        try:
            import serial  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Thieu pyserial: chay  pip install pyserial") from exc
        self.port = port
        self.baud = baud
        self.name = port
        self._last_err = ""
        self._ser = None
        self._open(serial, timeout)

    def _open(self, serial, timeout: float) -> None:
        self._ser = serial.Serial()
        self._ser.port = self.port
        self._ser.baudrate = self.baud
        self._ser.timeout = timeout
        self._ser.write_timeout = 1.0
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()
        # Mo cong thuong lam ESP32 RESET (mach auto-reset). Do bo qua rac luc ROM
        # khoi dong va doi ESP32 gui $BOOT len.
        self._ser.reset_input_buffer()

    def reopen(self) -> None:
        import serial  # type: ignore

        try:
            self.close()
        except Exception:
            pass
        self._open(serial, self._ser.timeout if self._ser else 0.0)

    def write(self, data: bytes) -> None:
        if self._ser is None:
            return
        try:
            self._ser.write(data)
        except Exception as exc:  # pragma: no cover - phu thuoc phan cung
            self._last_err = str(exc)

    def read(self, n: int = 512) -> bytes:
        if self._ser is None:
            return b""
        try:
            return self._ser.read(n) or b""
        except Exception as exc:  # pragma: no cover
            self._last_err = str(exc)
            return b""

    @property
    def ok(self) -> bool:
        return self._ser is not None and self._ser.is_open and not self._last_err

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None


class ProcessEspLink(Link):
    """Noi voi "ESP32 ao" la chuong trinh ``firmware/esp32/sim`` (bien dich tu
    chinh firmware that). Dung de chay thu toan bo he thong khong can phan cung."""

    def __init__(self, cmd: Sequence[str]):
        self.name = "sim:" + os.path.basename(cmd[0])
        self._p = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = False
        self._th = threading.Thread(target=self._reader, daemon=True)
        self._th.start()

    def _reader(self) -> None:
        assert self._p.stdout is not None
        while not self._stop:
            chunk = self._p.stdout.read(256)
            if not chunk:
                break
            with self._lock:
                self._buf.extend(chunk)

    def write(self, data: bytes) -> None:
        if self._p.stdin is None or self._p.poll() is not None:
            return
        try:
            self._p.stdin.write(data)
            self._p.stdin.flush()
        except Exception:
            pass

    def read(self, n: int = 512) -> bytes:
        with self._lock:
            out = bytes(self._buf[:n])
            del self._buf[:n]
        return out

    def control(self, cmd: str) -> None:
        self.write(("!" + cmd.strip() + "\n").encode("ascii", "ignore"))

    @property
    def ok(self) -> bool:
        return self._p.poll() is None

    def close(self) -> None:
        self._stop = True
        try:
            if self._p.stdin:
                self._p.stdin.write(b"!QUIT\n")
                self._p.stdin.flush()
        except Exception:
            pass
        try:
            self._p.wait(timeout=1.0)
        except Exception:
            self._p.kill()


def find_sim_binary(root: Optional[str] = None) -> Optional[str]:
    """Tim file ``sim`` da bien dich (make sim) trong firmware/esp32."""
    here = os.path.dirname(os.path.abspath(__file__))
    base = root or os.path.join(os.path.dirname(here), "firmware", "esp32")
    for cand in ("sim", "sim.exe"):
        p = os.path.join(base, cand)
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None


# ---------------------------------------------------------------------------
class EspChannel:
    """Boc mot ``Link`` + bo giai ma + dem loi, dung cho ca hai chieu."""

    def __init__(self, link: Link, hb_period_s: float = 0.25):
        self.link = link
        self.rx = FrameParser()
        self.hb_period = hb_period_s
        self._t_last_hb = 0.0
        self.tx_count = 0
        self.rx_count = 0
        self.started = time.monotonic()

    # ---- gui -------------------------------------------------------
    def send(self, ftype: str, *args) -> str:
        frame = encode_frame(ftype, args)
        self.link.write(frame.encode("ascii"))
        self.tx_count += 1
        return frame

    def beat(self) -> None:
        """Gui $HB neu den han (watchdog phia ESP32 la 800 ms)."""
        now = time.monotonic()
        if now - self._t_last_hb >= self.hb_period:
            self._t_last_hb = now
            self.send("HB", int((now - self.started) * 1000))

    def control(self, cmd: str) -> None:
        self.link.control(cmd)

    # ---- nhan ------------------------------------------------------
    def poll(self, max_msgs: int = 128) -> List[Msg]:
        data = self.link.read()
        if not data:
            return []
        msgs = self.rx.feed(data)
        self.rx_count += len(msgs)
        return msgs[:max_msgs]

    @property
    def link_errors(self) -> int:
        return self.rx.errors

    @property
    def ok(self) -> bool:
        return self.link.ok

    def close(self) -> None:
        self.link.close()


def iter_args(msg: Msg) -> Iterable[str]:  # pragma: no cover - tien loi
    return msg.args


if __name__ == "__main__":  # pragma: no cover - tu kiểm tra nhanh giao thuc
    f = encode_frame("CUP", [1])
    print("encode CUP,1 ->", repr(f))
    assert f == "$CUP,1*45\n", f
    p = FrameParser()
    got = p.feed(b"garbage\x00\xff" + f.encode() + b"$PUMP,60*d4\n$CUP,1*99\n")
    print("decoded:", [m.raw.strip() for m in got], "| loi:", p.errors)
    sys.exit(0)
