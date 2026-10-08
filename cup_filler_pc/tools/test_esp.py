#!/usr/bin/env python3
r"""KIEM CHUNG toan bo ket noi MAY TINH <-> ESP32 (khong can phan cung).

Chay:  python3 tools/test_esp.py

Cac bai:
  [1] PROTOCOL  : ma/giai ma khuung tin, gia tri checksum trung voi firmware C
  [2] PARITY    : cung mot kich ban -> ESP32 ao C++ va ESP32 ao Python phai
                  cho CUNG mot chuong trinh ban tin (chong lech hai ban cai dat)
  [3] HANDSHAKE : CUP,1 -> camera bat -> nhan dien -> ACK,1 -> ARMED
  [4] FILL      : nhan nut muc -> rot du the tich -> DONE (chay FSM that)
  [5] SAFETY    : nha coc giua chung -> cat bom, tat camera
  [6] WATCHDOG  : may tinh ngung gui $HB -> ESP32 tu cat bom

Neu da chay  make -C firmware/esp32 sim  thi bai [2] doi chieu ca hai ban;
neu chua, chi chay ban Python (va bao cho ban biet).
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.camera import Camera                                   # noqa: E402
from cupfiller.config import load_config                              # noqa: E402
from cupfiller.controller import State                                # noqa: E402
from cupfiller.detection import CupDetector                           # noqa: E402
from cupfiller.esp_sim import SimEspLink, SimRig                      # noqa: E402
from cupfiller.host_app import HostApp, HostState                     # noqa: E402
from cupfiller.pump import open_pump                                  # noqa: E402
from cupfiller.serial_link import (                                   # noqa: E402
    EspChannel, FrameParser, ProcessEspLink, checksum, encode_frame, find_sim_binary,
)
from cupfiller.synthetic import CupScene, SyntheticCupCamera          # noqa: E402

PASS = 0
FAIL = 0


def check(cond, what, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (what, extra))


def make_link(kind):
    if kind == "c":
        path = find_sim_binary()
        if not path:
            raise RuntimeError("chua bien dich ESP32 ao C++ (make -C firmware/esp32 sim)")
        return ProcessEspLink([path])
    return SimEspLink()


# ---------------------------------------------------------------------------
def t_protocol():
    print("[1] PROTOCOL - ma/giai ma khung tin UART")
    # gia tri "known answer" giong het test/test_firmware.cpp ben C
    check(encode_frame("CUP", [1]) == "$CUP,1*45\n", "$CUP,1*45",
          "-> %r" % encode_frame("CUP", [1]))
    check(encode_frame("ACK") == "$ACK*cf\n", "$ACK*cf", "-> %r" % encode_frame("ACK"))
    check(checksum("CUP,1") == 0x45, "checksum CUP,1 = 0x45")

    p = FrameParser()
    good = encode_frame("CUP", [1]).encode() + encode_frame("PUMP", [60]).encode()
    msgs = p.feed(b"\x00\xff rac \r\n" + good + b"$CUP,1*99\n$CU" + encode_frame("HB", [1, "IDLE"]).encode())
    kinds = [m.type for m in msgs]
    check(kinds == ["CUP", "PUMP", "HB"], "giai ma 3 khung hop le", "-> %s" % kinds)
    check(p.errors == 1, "dem dung 1 loi checksum", "-> %d" % p.errors)
    check(msgs[2].int_arg(0) == 1 and msgs[2].str_arg(1) == "IDLE", "tach tham so HB")
    check(FrameParser().feed(b"$" + b"A" * 200 + b"*00\n") == [], "khung qua dai bi bo qua")


# ---------------------------------------------------------------------------
def run_scenario(link):
    """Kich ban chuan: ket noi -> dat coc -> xac nhan -> nhan nut 3 -> bom -> xong.

    Tra ve danh sach (TYPE, args) ma ESP32 gui len (bo $HB vi phu thuoc thoi diem).
    """
    ch = EspChannel(link, hb_period_s=0.25)
    ev = []

    def pump(seconds, until=None):
        t0 = time.time()
        while time.time() - t0 < seconds:
            for m in ch.poll():
                if m.type != "HB":
                    ev.append((m.type, tuple(m.args)))
            ch.beat()
            time.sleep(0.01)
            if until and until():
                return

    ch.send("HELLO", "1")
    pump(0.4)
    link.control("CUP 1")
    pump(1.5, until=lambda: any(t == "CUP" for t, _ in ev))
    ch.send("ACK", 1)
    pump(0.3)
    link.control("BTN 3")
    pump(1.5, until=lambda: any(t == "START" for t, _ in ev))
    ch.send("PUMP", 60)
    pump(0.5)
    link.control("CUP 0")          # nha coc giua chung
    pump(0.8)
    ch.send("STATE", "DONE")
    pump(0.4)
    ch.close()
    return ev


def t_parity():
    print("[2] PARITY - ESP32 ao C++ va ESP32 ao Python")
    py = run_scenario(make_link("py"))
    print("   Python:", " ".join(t for t, _ in py))
    try:
        c = run_scenario(make_link("c"))
    except RuntimeError as exc:
        print("   [skip] %s" % exc)
        check(any(t == "START" and a == ("200",) for t, a in py), "Python sim: nut 3 -> START,200")
        check(any(t == "STOP" and a == ("CUP_REMOVED",) for t, a in py), "Python sim: nha coc -> STOP")
        return
    print("   C++   :", " ".join(t for t, _ in c))
    check([t for t, _ in py] == [t for t, _ in c], "hai ban cho CUNG chuong trinh ban tin")
    for want in [("CUP", ("1",)), ("LVL", ("200",)), ("START", ("200",)),
                 ("PUMP", ("60",)), ("STOP", ("CUP_REMOVED",)), ("CUP", ("0",)),
                 ("DONE", None)]:
        typ, args = want
        ok_py = any(t == typ and (args is None or a == args) for t, a in py)
        ok_c = any(t == typ and (args is None or a == args) for t, a in c)
        check(ok_py and ok_c, "ca hai ban deu co %s%s" % (typ, args or ""))


# ---------------------------------------------------------------------------
def make_app(kind="py"):
    cfg = load_config(None)
    cfg.set("control.pump.type", "uart")
    cfg.set("camera.backend", "synthetic")
    cfg.set("control.loop.fps", 15)
    link = make_link(kind)
    ch = EspChannel(link, hb_period_s=float(cfg.get("esp32.hb_period_s", 0.25)))
    scene = SyntheticCupCamera(CupScene(), fps=15.0)
    scene.remove_cup()                       # khay TRONG luc bat dau
    open_cam = lambda: Camera("synthetic", cfg, scene=scene)
    pump = open_pump(cfg, None, channel=ch)
    app = HostApp(cfg, ch, open_cam, pump, detector=CupDetector(cfg), sim_scene=scene)
    return app, ch, scene, pump, SimRig(ch, scene)


def t_handshake_and_fill():
    print("[3+4] HANDSHAKE + FILL - chay FSM rot that tren anh gia lap")
    app, ch, scene, pump, rig = make_app()
    dt = 1.0 / 15.0
    check(app.state == HostState.WAIT_CUP and not app.slot.open, "ban dau: camera TAT")

    t0 = time.time()
    while time.time() - t0 < 1.0:
        app.tick(dt)
    check(not app.slot.open, "chua co coc -> camera van TAT")

    rig.cup(True)                            # ESP32 bao co coc
    t0 = time.time()
    while app.state != HostState.ARMED and time.time() - t0 < 5.0:
        app.tick(dt)
    check(app.slot.open, "nhan CUP,1 -> camera BAT")
    check(app.state == HostState.ARMED, "nhan dien thay coc -> ARMED (cho phep nut/mic)")
    # doi ban tin $HB ke tiep de biet chac ESP32 ben kia cung da ARMED
    t0 = time.time()
    while app.esp["state"] != "ARMED" and time.time() - t0 < 1.5:
        app.tick(dt)
    check(app.esp["armed"] == 1 and app.esp["state"] == "ARMED",
          "ESP32 cung da o trang thai ARMED", "-> %s" % app.esp)

    rig.button(3)                            # nhan nut muc 3 = 200 ml
    t0 = time.time()
    while app.state != HostState.FILL and time.time() - t0 < 3.0:
        app.tick(dt)
    check(app.state == HostState.FILL, "nhan nut -> may tinh bat dau rot")
    check(abs(app.ctl.preset_ml - 200) < 1e-6, "preset = 200 ml", "-> %s" % app.ctl.preset_ml)

    t0 = time.time()
    while app.ctl.state not in (State.DONE, State.FAULT) and time.time() - t0 < 40.0:
        app.tick(dt)
    ok = app.ctl.state == State.DONE
    check(ok, "rot xong (DONE)", "-> state=%s alert=%s" % (app.ctl.state.value, app.ctl.alert))
    ml = scene.volume_ml
    check(abs(ml - 200) <= 8.0, "the tich that 200+-8 ml", "-> %.1f ml" % ml)
    check(pump.last_duty == 0, "xong rot -> lenh bom cuoi cung la 0%", "-> %s" % pump.last_duty)
    check(app.state == HostState.ARMED, "xong rot -> ve ARMED cho lan sau")
    print("   ket qua rot: %s" % app.last_report)

    # ---- [5] nha coc giua chung ----
    print("[5] SAFETY - nha coc khi dang rot")
    rig.button(2)                            # rot 150 ml
    t0 = time.time()
    while app.ctl.state not in (State.COARSE, State.FINE, State.PRIME) and time.time() - t0 < 5.0:
        app.tick(dt)
    check(app.state == HostState.FILL, "dang rot truoc khi nha coc")
    rig.cup(False)                           # nha coc ra
    t0 = time.time()
    while app.state != HostState.WAIT_CUP and time.time() - t0 < 3.0:
        app.tick(dt)
    check(app.state == HostState.WAIT_CUP, "nha coc -> ve WAIT_CUP")
    check(pump.last_duty == 0, "nha coc -> lenh bom = 0%", "-> %s" % pump.last_duty)
    t0 = time.time()
    while app.slot.open and time.time() - t0 < 3.0:
        app.tick(dt)
    check(not app.slot.open, "nha coc -> camera duoc TAT")
    ch.close()


# ---------------------------------------------------------------------------
def t_watchdog():
    print("[6] WATCHDOG - may tinh im lang -> ESP32 tu cat bom")
    link = make_link("py")
    ch = EspChannel(link, hb_period_s=0.25)
    ev = []

    def pump(seconds, beat=True):
        t0 = time.time()
        while time.time() - t0 < seconds:
            for m in ch.poll():
                ev.append((m.type, tuple(m.args)))
            if beat:
                ch.beat()
            time.sleep(0.01)

    ch.send("HELLO", "1")
    pump(0.3)
    link.control("CUP 1")
    pump(1.0)
    ch.send("ACK", 1)
    pump(0.3)
    link.control("BTN 3")
    pump(0.6)
    ch.send("PUMP", 70)
    pump(0.4)
    check(any(t == "START" for t, _ in ev), "bat dau rot truoc khi test watchdog")
    check(getattr(link.sim, "relay_on", False) or link.sim.duty == 70,
          "ESP32 giu duty 70%", "-> duty=%s" % getattr(link.sim, "duty", "?"))

    ev.clear()
    pump(2.0, beat=False)                    # may tinh "chet": khong gui $HB
    check(any(t == "STOP" and a == ("PC_TIMEOUT",) for t, a in ev), "ESP32 bao STOP,PC_TIMEOUT")
    check(link.sim.duty == 0, "ESP32 tu dua duty ve 0", "-> %s" % link.sim.duty)
    ch.close()


# ---------------------------------------------------------------------------
def main():
    print("== KIEM CHUNG MAY TINH <-> ESP32 ==")
    t_protocol()
    t_parity()
    t_handshake_and_fill()
    t_watchdog()
    print("\nKET QUA: %d dat, %d loi" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
