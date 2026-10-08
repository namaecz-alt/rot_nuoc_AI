r"""Dieu phoi phia MAY TINH khi lam viec voi ESP32 qua UART.

Trinh tu bat buoc (dung theo yeu cau de bai):

  1. ESP32 thay COC tren cam bien  -> gui ``$CUP,1``.
  2. May tinh NHAN duoc goi tin do MOI **bat camera** va chay nhan dien.
  3. Thay dung coc trong anh -> gui ``$ACK,1`` (khong thay -> ``$ACK,0``).
  4. ESP32 duoc xac nhan -> sang ARMED, cho phep nhan NUT MUC hoac noi MIC.
  5. ESP32 gui ``$START,<ml>`` -> may tinh chay FSM rot va gui ``$PUMP,<duty>``
     xuong de ESP32 dong relay bom (tich cuc CAO).
  6. Xong -> ``$STATE,DONE``; nha coc / mat lien lac -> cat bom ca hai phia.

Camera duoc MO/DONG theo su kien (khong mo san) de tiet kiem CPU: khi khong
co coc, may tinh khong doc khung hinh nao.
"""
from __future__ import annotations

import time
from collections import deque
from enum import Enum
from typing import Callable, List, Optional

from .controller import FillController, State
from .serial_link import EspChannel, Msg

__all__ = ["HostState", "HostApp", "CameraSlot"]


class HostState(str, Enum):
    WAIT_CUP = "WAIT_CUP"        # camera TAT, doi ESP32 bao co coc
    CONFIRM_CUP = "CONFIRM_CUP"  # camera BAT, dang kiem tra coc trong anh
    ARMED = "ARMED"              # da xac nhan, doi nut bam / mic
    FILL = "FILL"                # dang rot
    ERROR = "ERROR"


class CameraSlot:
    """O cam camera: cho phep mo/dong camera ma van giu nguyen FillController."""

    def __init__(self) -> None:
        self.cam = None

    def read(self):
        if self.cam is None:
            return False, None
        return self.cam.read()

    def release(self) -> None:
        if self.cam is not None:
            try:
                self.cam.release()
            except Exception:
                pass
            self.cam = None

    @property
    def synthetic(self):
        return getattr(self.cam, "synthetic", None)

    @property
    def open(self) -> bool:
        return self.cam is not None


class HostApp:
    def __init__(
        self,
        cfg,
        channel: EspChannel,
        open_camera: Callable[[], object],
        pump,
        detector=None,
        log: Callable[[str], None] = print,
        sim_scene=None,
    ):
        self.cfg = cfg
        self.ch = channel
        self.pump = pump
        self.log = log
        self.slot = CameraSlot()
        self._open_camera = open_camera
        # Chi "soi" canh gia lap tu muc bom ESP32 bao ve khi bom di qua UART.
        # Neu dung SimPump (bom tu chay trong may tinh) thi de SimPump tu dieu khien,
        # khong duoc ghi de - khong thi moi nhip $HB se dat luu luong ve 0.
        self.sim_scene = sim_scene if hasattr(pump, "last_duty") else None

        self.ctl = FillController(cfg, self.slot, pump, detector=detector)
        self.state = HostState.WAIT_CUP
        self._t_state = 0.0
        self._confirm_hits = 0
        self._idle_t = 0.0
        self._last_fsm_state: Optional[State] = None
        self.last_report: dict = {}
        self.last_error = ""
        self.events: deque = deque(maxlen=400)

        e = cfg.get("esp32", {}) if hasattr(cfg, "get") else {}
        self.confirm_frames = int(e.get("confirm_frames", 3))
        self.confirm_timeout_s = float(e.get("confirm_timeout_s", 5.0))
        self.keep_camera_open = bool(e.get("keep_camera_open", True))
        self.idle_release_s = float(e.get("idle_release_s", 1.0))
        self.presets: List[int] = list(self.ctl.presets)

        # trang thai ESP32 (doc tu $HB)
        self.esp = {"state": "?", "cup": 0, "armed": 0, "duty": 0, "online": False}
        self._hello_done = False   # chan vong lap BOOT -> HELLO -> BOOT

    # ------------------------------------------------------------------
    def _go(self, st: HostState) -> None:
        if st == self.state:
            return
        self.log("[host] %s -> %s" % (self.state.value, st.value))
        self.state = st
        self._t_state = 0.0
        if st in (HostState.WAIT_CUP,):
            self._confirm_hits = 0
            self._idle_t = 0.0

    def _note(self, text: str) -> None:
        self.events.append("%.1f %s" % (time.monotonic() % 10000, text))
        self.log("[esp] " + text)

    # ------------------------------------------------------------------
    def handshake(self, force: bool = False) -> None:
        """Gui HELLO + danh sach muc rot xuong ESP32.

        Chi gui MOT LAN cho moi lan ket noi: ESP32 tra loi HELLO bang BOOT, neu
        cu thay BOOT lai gui HELLO thi hai ben se goi nhau mai mai.
        """
        if self._hello_done and not force:
            return
        self._hello_done = True
        self.ch.send("HELLO", "1")
        self.ch.send("CFG", ";".join(str(int(p)) for p in self.presets))

    def reset_link(self) -> None:
        """Goi sau khi mo lai cong UART de bat tay lai tu dau."""
        self._hello_done = False

    # ------------------------------------------------------------------
    def tick(self, dt: Optional[float] = None) -> dict:
        dt = dt if dt is not None else 1.0 / max(self.ctl.fps, 1.0)
        self._t_state += dt
        self.ch.beat()                       # giu nhip de ESP32 khong cat bom

        for m in self.ch.poll():
            self._on_msg(m)

        if self.state == HostState.WAIT_CUP:
            self._idle_t += dt
            if self.slot.open and self._idle_t >= self.idle_release_s:
                self.log("[host] khong co coc -> tat camera")
                self.slot.release()
        elif self.state == HostState.CONFIRM_CUP:
            self._tick_confirm(dt)
        elif self.state == HostState.ARMED:
            self.ctl.tick(dt)                # chi de co anh xem truc tiep
        elif self.state == HostState.FILL:
            self._tick_fill(dt)

        return self.snapshot()

    # ------------------------------------------------------------------
    def _tick_confirm(self, dt: float) -> None:
        tel = self.ctl.tick(dt)
        if tel["cup"]:
            self._confirm_hits += 1
        else:
            self._confirm_hits = 0
        if self._confirm_hits >= self.confirm_frames:
            self.log("[host] thay coc trong anh -> gui ACK,1 (cho phep nhan nut/mic)")
            self.ch.send("ACK", 1)
            self._go(HostState.ARMED)
            return
        if self._t_state > self.confirm_timeout_s:
            self.log("[host] khong thay coc trong anh -> gui ACK,0")
            self.ch.send("ACK", 0)
            self.slot.release()
            self._go(HostState.WAIT_CUP)

    def _tick_fill(self, dt: float) -> None:
        tel = self.ctl.tick(dt)
        st = self.ctl.state
        if st != self._last_fsm_state:
            self._last_fsm_state = st
            self.ch.send("STATE", st.value)
        if st == State.DONE:
            self.last_report = dict(self.ctl.final_report or {})
            self._note("rot xong: %s" % self.last_report)
            self._go(HostState.ARMED)
        elif st == State.FAULT:
            self.last_error = self.ctl.alert
            self._note("LOI tu phia may tinh: %s" % self.ctl.alert)
            self.ch.send("STATE", "FAULT")
            self.pump.off()
            self._go(HostState.ARMED)
        elif st == State.IDLE:
            self._go(HostState.ARMED)
        return tel

    # ------------------------------------------------------------------
    def _on_msg(self, m: Msg) -> None:
        t = m.type
        if t == "BOOT":
            if not self._hello_done:
                self._note("ESP32 khoi dong (fw %s)" % m.str_arg(0, "?"))
            self.handshake()
        elif t == "HB":
            self.esp = {
                "state": m.str_arg(1, "?"),
                "cup": m.int_arg(2),
                "armed": m.int_arg(3),
                "duty": m.int_arg(4),
                "online": True,
            }
            self._mirror_flow(self.esp["duty"])
        elif t == "CUP":
            present = m.int_arg(0) == 1
            self._note("cam bien coc = %d" % present)
            self.esp["cup"] = 1 if present else 0
            if present:
                self._on_cup_placed()
            else:
                self._on_cup_removed()
        elif t == "START":
            ml = m.int_arg(0, int(self.ctl.preset_ml))
            self._note("yeu cau rot %d ml" % ml)
            self._start_fill(ml)
        elif t == "LVL":
            self.ctl.set_preset(m.int_arg(0, self.ctl.preset_ml))
        elif t == "STOP":
            why = m.str_arg(0, "?")
            self._note("ESP32 dung rot: %s" % why)
            if self.state == HostState.FILL:
                self.ctl.stop("ESP:" + why)
                self.pump.off()
            if why in ("CUP_REMOVED", "PC_TIMEOUT", "ACK_LOST"):
                self._go(HostState.WAIT_CUP)
            elif self.state != HostState.FILL:
                pass
            else:
                self._go(HostState.ARMED)
        elif t == "DONE":
            self._note("ESP32 xac nhan ket thuc mot lan rot")
        elif t == "ERR":
            self.last_error = m.str_arg(0, "?")
            self._note("ESP32 bao loi: %s" % self.last_error)
        elif t in ("BTN", "PUMP", "CFG"):
            if t == "PUMP":
                self.esp["duty"] = m.int_arg(0, self.esp["duty"])
                self._mirror_flow(self.esp["duty"])

    def _on_cup_placed(self) -> None:
        if self.state in (HostState.FILL,):
            return
        if self.state == HostState.ARMED:
            return
        if not self.slot.open:
            self.log("[host] nhan CUP,1 -> BAT camera")
            try:
                self.slot.cam = self._open_camera()
            except Exception as exc:
                self.last_error = "CAMERA: %s" % exc
                self.log("[host] mo camera that bai: %s" % exc)
                self.ch.send("ACK", 0)
                self._go(HostState.WAIT_CUP)
                return
        self._confirm_hits = 0
        self._go(HostState.CONFIRM_CUP)

    def _on_cup_removed(self) -> None:
        if self.state == HostState.FILL:
            self.ctl.stop("CUP_REMOVED")
        self.pump.off()
        self.slot.release()
        self._go(HostState.WAIT_CUP)

    def _start_fill(self, ml: int) -> None:
        if self.state != HostState.ARMED:
            self.log("[host] bo qua START vi dang o %s" % self.state.value)
            return
        self.ctl.set_preset(ml)
        self.ctl.start()
        self._last_fsm_state = None
        self._go(HostState.FILL)

    def _mirror_flow(self, duty: int) -> None:
        """Che do mo phong: dung muc bom ESP32 bao ve de day nuoc len canh gia lap."""
        if self.sim_scene is None:
            return
        try:
            self.sim_scene.set_flow(self.pump.flow_at(max(0, min(100, duty)) / 100.0))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def stop_fill(self, why: str = "PC_STOP") -> None:
        self.ctl.stop(why)
        self.pump.off()
        self.ch.send("PUMP", 0)
        self._go(HostState.ARMED if self.esp.get("cup") else HostState.WAIT_CUP)

    def snapshot(self) -> dict:
        tel = self.ctl.telemetry()
        tel.update(
            {
                "host_state": self.state.value,
                "camera_open": self.slot.open,
                "esp": dict(self.esp),
                "link_ok": self.ch.ok,
                "link_tx": self.ch.tx_count,
                "link_rx": self.ch.rx_count,
                "link_errors": self.ch.link_errors,
                "last_error": self.last_error,
                "report": self.last_report or tel.get("report") or {},
            }
        )
        return tel
