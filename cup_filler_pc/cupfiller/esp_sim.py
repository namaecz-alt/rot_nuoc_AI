r"""ESP32 AO viet bang Python - ban dich sat voi ``firmware/esp32/device_fsm.cpp``.

Dung khi ban muon chay thu toan bo he thong (camera + nhan dien + dieu khien)
ma khong co ESP32 that va khong co trinh bien dich g++ de dung ban ``sim`` C++.

    from cupfiller.esp_sim import SimEspLink
    link = SimEspLink()
    link.control("CUP 1")        # gia lap dat coc len cam bien
    link.control("BTN 3")        # gia lap nhan nut muc 3

Moi quan he: file nay KHONG thay the firmware; no chi giup chay demo. Do do
``tools/test_esp.py`` co bai kiem tra DOI CHIEU: cung mot kich ban, ban C++ va
ban Python phai cho cung mot chuong trinh ban tin.
"""
from __future__ import annotations

import time
from typing import List, Optional

from .serial_link import FrameParser, Link, encode_frame

__all__ = ["EspSimulator", "SimEspLink", "SimRig"]

# ---- tham so giong config.h cua firmware -------------------------------
CUP_DEBOUNCE_MS = 150
BTN_DEBOUNCE_MS = 25
BTN_LONG_PRESS_MS = 900
ACK_TIMEOUT_MS = 6000
PC_TIMEOUT_MS = 800
HB_PERIOD_MS = 250
CUP_RETRY_MS = 2500
FILL_MAX_MS = 60000
SOFTPWM_PERIOD_MS = 400
PRESETS = [100, 150, 200, 250, 300]

IDLE, WAIT_ACK, ARMED, FILLING = "IDLE", "WAIT_ACK", "ARMED", "FILLING"


class EspSimulator:
    def __init__(self, presets: Optional[List[int]] = None):
        self.presets = list(presets or PRESETS)
        self.t = 0.0                       # ms
        self.state = IDLE
        self.t_state = 0.0
        self.last_pc = 0.0
        self.pc_seen = False
        self.last_hb = 0.0
        self.last_cup_report = 0.0
        self.cup_present = False
        self._cup_raw = False
        self._cup_since = 0.0
        self.duty = 0
        self.level_ml = self.presets[2]
        self.ack_fail = 0
        self.wait_cup_cycle = False
        self.errors = 0
        self.out = bytearray()
        self._send("BOOT", "sim-1.0")
        self.rx = FrameParser()
        self._btn_release: List = []       # (thoi diem tha nut, ten nut)
        self._btn = {}                     # ten nut -> [active, since, long_fired]
        self.relay_on = False

    # ---- dau vao gia lap -------------------------------------------
    def set_cup(self, present: bool) -> None:
        self._cup_raw = bool(present)
        self._cup_since = self.t

    def press_button(self, which, long_press: bool = False) -> None:
        """which: 1..5 = nut muc, 0 = nut DUNG."""
        self._btn[which] = [True, self.t, False]
        hold = (BTN_LONG_PRESS_MS + 300) if long_press else 80
        self._btn_release.append((self.t + hold, which))

    def voice(self, cmd: int) -> None:
        self._on_voice(int(cmd))

    # ---- kenh UART -------------------------------------------------
    def write_from_pc(self, data: bytes) -> None:
        for m in self.rx.feed(data):
            self._handle(m.type, m.args)

    def read_to_pc(self) -> bytes:
        out = bytes(self.out)
        self.out.clear()
        return out

    def _send(self, ftype: str, *args) -> None:
        self.out.extend(encode_frame(ftype, args).encode("ascii"))

    # ---- vong lap --------------------------------------------------
    def tick(self, dt_s: float) -> None:
        self.t += max(0.0, dt_s) * 1000.0
        t = self.t

        # nut bam duoc tha ra theo lich
        still = []
        for when, which in self._btn_release:
            if t >= when:
                self._release(which)
            else:
                still.append((when, which))
        self._btn_release = still

        self._poll_cup(t)
        for which, b in list(self._btn.items()):
            self._poll_btn(which, b, t)

        # watchdog lien lac
        if self.pc_seen and (t - self.last_pc) > PC_TIMEOUT_MS:
            if self.state == FILLING:
                self._abort("PC_TIMEOUT", IDLE)
            elif self.state in (ARMED, WAIT_ACK):
                self._go(IDLE)
            self._error("PC_TIMEOUT")

        if self.state == IDLE:
            self._set_duty(0)
            if self.cup_present and self.pc_online() and not self.wait_cup_cycle and \
                    (t - self.last_cup_report) >= CUP_RETRY_MS:
                self.last_cup_report = t
                self._send("CUP", 1)
                self._go(WAIT_ACK)
        elif self.state == WAIT_ACK:
            self._set_duty(0)
            if not self.cup_present:
                self._on_cup(False)
            elif (t - self.t_state) > ACK_TIMEOUT_MS:
                self._error("ACK_TIMEOUT")
                self._ack_failed()
                self._go(IDLE)
        elif self.state == ARMED:
            self._set_duty(0)
            if not self.cup_present:
                self._on_cup(False)
        elif self.state == FILLING:
            if not self.cup_present:
                self._on_cup(False)
            elif (t - self.t_state) > FILL_MAX_MS:
                self._abort("FILL_TIMEOUT", ARMED)

        self._softpwm(t)
        self._hb(t)

    # ---- chi tiet --------------------------------------------------
    def pc_online(self) -> bool:
        return self.pc_seen and (self.t - self.last_pc) <= PC_TIMEOUT_MS

    def _go(self, st: str) -> None:
        self.state = st
        self.t_state = self.t

    def _error(self, code: str) -> None:
        self.errors += 1
        self._send("ERR", code)

    def _poll_cup(self, t: float) -> None:
        if self._cup_raw != self.cup_present and (t - self._cup_since) >= CUP_DEBOUNCE_MS:
            self.cup_present = self._cup_raw
            self._on_cup(self.cup_present)

    def _on_cup(self, present: bool) -> None:
        if not present:
            self.cup_present = False
            self.ack_fail = 0
            self.wait_cup_cycle = False
            if self.state == FILLING:
                self._abort("CUP_REMOVED", IDLE)
            elif self.state != IDLE:
                self._go(IDLE)
            self._send("CUP", 0)
            return
        if self.state == IDLE:
            if self.pc_online():
                self.last_cup_report = self.t
                self._send("CUP", 1)
                self._go(WAIT_ACK)
            else:
                self.last_cup_report = self.t - CUP_RETRY_MS + 200

    def _poll_btn(self, which, b: list, t: float) -> None:
        active, since, long_fired = b
        if active and not long_fired and which != 0 and (t - since) >= BTN_LONG_PRESS_MS:
            b[2] = True
            self._on_level(which - 1, True)

    def _release(self, which) -> None:
        b = self._btn.get(which)
        if not b:
            return
        long_fired = b[2]
        del self._btn[which]
        if which == 0:
            self._send("BTN", "0,DOWN")
            if self.state == FILLING:
                self._abort("BTN_STOP", ARMED)
            else:
                self._send("STOP", "BTN_IDLE")
        elif not long_fired:
            self._send("BTN", "%d,DOWN" % which)
            self._on_level(which - 1, False)

    def _on_level(self, idx: int, long_press: bool) -> None:
        if idx < 0 or idx >= len(self.presets):
            return
        ml = self.presets[idx]
        if long_press:
            self.level_ml = ml
            self._send("BTN", "%d,LONG" % (idx + 1))
            self._send("LVL", ml)
            return
        self._on_voice(idx)

    def _on_voice(self, cmd: int) -> None:
        if 0 <= cmd < len(self.presets):
            ml = self.presets[cmd]
            self.level_ml = ml
            self._send("LVL", ml)
            self._start(ml)
        elif cmd == 5:
            self._start(self.level_ml)
        elif cmd == 6:
            if self.state == FILLING:
                self._abort("VOICE_STOP", ARMED)
            else:
                self._send("STOP", "VOICE_IDLE")

    def _start(self, ml: int) -> None:
        if self.state == FILLING:
            self._send("ERR", "BUSY")
            return
        if self.state != ARMED:
            self._send("ERR", "NOT_ACKED" if self.state == WAIT_ACK else "NOT_ARMED")
            return
        if not self.pc_online():
            self._error("PC_TIMEOUT")
            self._go(IDLE)
            return
        self._send("START", ml)
        self._go(FILLING)

    def _abort(self, why: str, nxt: str) -> None:
        self._set_duty(0)
        self._send("STOP", why)
        self._go(nxt)

    def _set_duty(self, d: int) -> None:
        d = max(0, min(100, int(d)))
        if d == self.duty:
            return
        self.duty = d
        self._send("PUMP", d)

    def _softpwm(self, t: float) -> None:
        if self.state != FILLING or self.duty == 0:
            self.relay_on = False
        elif self.duty >= 100:
            self.relay_on = True
        else:
            self.relay_on = (t % SOFTPWM_PERIOD_MS) < (SOFTPWM_PERIOD_MS * self.duty / 100.0)

    def _hb(self, t: float) -> None:
        if (t - self.last_hb) < HB_PERIOD_MS:
            return
        self.last_hb = t
        armed = 1 if self.state in (ARMED, FILLING) else 0
        self._send(
            "HB", int(t), self.state, 1 if self.cup_present else 0, armed, self.duty
        )

    def _ack_failed(self) -> None:
        self.last_cup_report = self.t
        self.ack_fail += 1
        if self.ack_fail >= 3:
            self.wait_cup_cycle = True

    # ---- xu ly tin tu may tinh -------------------------------------
    def _handle(self, ftype: str, args: List[str]) -> None:
        self.last_pc = self.t
        self.pc_seen = True
        if ftype == "HELLO":
            self._send("BOOT", "sim-1.0")
            self._hb(self.t)
            if self.cup_present and self.state == IDLE:
                self.last_cup_report = self.t
                self._send("CUP", 1)
                self._go(WAIT_ACK)
        elif ftype == "CFG":
            vals = [v for v in (args[0].split(";") if args else []) if v.isdigit()]
            if vals:
                self.presets = [int(v) for v in vals][:5]
                self.level_ml = self.presets[0]
            self._send("CFG", "OK")
        elif ftype == "ACK":
            ok = bool(args) and args[0] not in ("0", "")
            if ok:
                if self.state == WAIT_ACK:
                    self._go(ARMED)
                    self.ack_fail = 0
                    self.wait_cup_cycle = False
            else:
                if self.state == FILLING:
                    self._abort("ACK_LOST", IDLE)
                self._error("NO_CUP_VISION")
                self._ack_failed()
                self._go(IDLE)
        elif ftype == "PUMP":
            if self.state == FILLING:
                try:
                    self._set_duty(int(args[0]))
                except (IndexError, ValueError):
                    pass
            else:
                self._set_duty(0)
        elif ftype == "STATE":
            st = args[0] if args else ""
            if st in ("DONE", "FAULT"):
                self._set_duty(0)
                self.relay_on = False
                if self.state == FILLING:
                    self._go(ARMED)
                if st == "DONE":
                    self._send("DONE", int(self.t - self.t_state))
                else:
                    self._error("PC_FAULT")


class SimEspLink(Link):
    """``Link`` bao quanh EspSimulator - giong giao dien voi SerialEspLink."""

    name = "pysim"

    def __init__(self, presets=None):
        self.sim = EspSimulator(presets)
        self._last = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        self.sim.tick(now - self._last)
        self._last = now

    def write(self, data: bytes) -> None:
        self._tick()
        self.sim.write_from_pc(data)

    def read(self, n: int = 512) -> bytes:
        self._tick()
        return self.sim.read_to_pc()[:n]

    def control(self, cmd: str) -> None:
        self._tick()
        parts = cmd.split()
        if not parts:
            return
        head = parts[0].upper()
        if head == "CUP":
            self.sim.set_cup(len(parts) > 1 and parts[1] == "1")
        elif head == "BTN":
            idx = int(parts[1]) if len(parts) > 1 else 1
            self.sim.press_button(idx, long_press=len(parts) > 2 and parts[2].upper() == "L")
        elif head == "VOICE":
            self.sim.voice(int(parts[1]) if len(parts) > 1 else 5)
        elif head == "HBGAP":
            # gia lap may tinh ngung gui nhip (khong dung trong vong chay binh thuong)
            self.sim.last_pc -= float(parts[1])

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
class SimRig:
    """Dieu khien "ban nhap" dau vao khi chay mo phong.

    Ngoai viec gia lap cam bien/nut/mic cho ESP32, no con dat/nha coc trong
    CANH GIA LAP cua camera de pipeline nhan dien nhin thay that su.
    """

    def __init__(self, channel, scene=None):
        self.ch = channel
        self.scene = scene
        self.cup_in = False

    def cup(self, present: bool) -> None:
        self.cup_in = bool(present)
        self.ch.control("CUP %d" % (1 if self.cup_in else 0))
        if self.scene is not None:
            if self.cup_in:
                self.scene.place_cup()
            else:
                self.scene.remove_cup()

    def button(self, index: int, long_press: bool = False) -> None:
        self.ch.control("BTN %d%s" % (index, " L" if long_press else ""))

    def stop(self) -> None:
        self.ch.control("BTN 0")

    def voice(self, cmd: int) -> None:
        self.ch.control("VOICE %d" % cmd)
