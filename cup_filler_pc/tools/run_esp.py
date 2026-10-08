#!/usr/bin/env python3
r"""MAY TINH <> ESP32: nhan goi tin "co coc" -> bat camera -> xac nhan -> rot.

Trinh tu chay (ESP32 chu dong, may tinh chi lam viec khi duoc goi):

    [1] ESP32 thay coc tren cam bien  ->  $CUP,1
    [2] May tinh BAT CAMERA, nhan dien coc trong anh
    [3] Thay coc -> $ACK,1 (khong thay -> $ACK,0)
    [4] ESP32 sang ARMED: cho phep NHAN NUT MUC hoac NOI MIC
    [5] ESP32 gui $START,<ml> -> may tinh chay FSM va gui $PUMP,<duty> xuong
        de ESP32 dong RELAY bom (tich cuc CAO)
    [6] Xong -> $STATE,DONE.  Nha coc / mat lien lac -> cat bom ca hai phia.

Chay:
    python tools/run_esp.py --port COM5 --camera 0     # phan cung that
    python tools/run_esp.py --sim                      # ESP32 ao (khong can nap)
    python tools/run_esp.py --port COM5 --monitor      # chi xem khung tin UART
    python tools/run_esp.py --sim --synthetic --no-window --auto-test

Phim trong cua so video:
    c  dat / nha coc (chi o che do mo phong)      x  nut DUNG
    1..5  nhan nut muc (mo phong)                 v  lenh giong noi "rot"
    s  bat dau rot tu phia may tinh (dry-run)     e  dung
    q  thoat
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.config import load_config                              # noqa: E402
from cupfiller.serial_link import (                                   # noqa: E402
    EspChannel, ProcessEspLink, SerialEspLink, find_sim_binary,
)
from cupfiller.pump import open_pump                                  # noqa: E402
from cupfiller.host_app import HostApp                                # noqa: E402
from cupfiller.detection import CupDetector, draw_overlay             # noqa: E402
from cupfiller.esp_sim import SimRig                                  # noqa: E402


# ---------------------------------------------------------------------------
def build_link(args, cfg):
    """Tao kenh UART: cong that, ESP32 ao (C++) hoac ESP32 ao (Python)."""
    if args.port:
        print("Mo UART %s @ %d baud..." % (args.port, args.baud), flush=True)
        link = SerialEspLink(args.port, args.baud)
        # Mach auto-reset lam ESP32 khoi dong lai khi mo cong -> cho no xong.
        time.sleep(1.2)
        return link, True
    if args.pysim:
        from cupfiller.esp_sim import SimEspLink

        print("Dung ESP32 ao (Python) - cupfiller/esp_sim.py", flush=True)
        return SimEspLink(), True
    sim = find_sim_binary()
    if sim:
        print("Dung ESP32 ao (C++): %s" % os.path.relpath(sim, ROOT), flush=True)
        return ProcessEspLink([sim]), True
    print("Khong tim thay firmware/esp32/sim. Chay  make -C firmware/esp32 sim  "
          "hoac dung --pysim (ESP32 ao viet bang Python).", flush=True)
    from cupfiller.esp_sim import SimEspLink

    return SimEspLink(), True


def build_camera_factory(cfg, args):
    """Tra ve (ham_mo_camera, canh_gia_lap|None). Camera mo theo su kien."""
    from cupfiller.camera import Camera

    scene = None
    if args.synthetic:
        from cupfiller.synthetic import CupScene, SyntheticCupCamera

        scene = SyntheticCupCamera(CupScene(), fps=float(cfg.get("control.loop.fps", 15)))
        scene.remove_cup()          # ban dau khay TRONG: doi ESP32 bao co coc
        cfg.set("camera.backend", "synthetic")

        def open_cam():
            cam = Camera("synthetic", cfg, scene=scene)
            return cam

        return open_cam, scene

    if args.video:
        cfg.set("camera.backend", "video")
        cfg.set("camera.source_file", args.video)
        return lambda: Camera("video", cfg), None

    cfg.set("camera.backend", "v4l2")
    dev = int(args.camera) if str(args.camera).isdigit() else args.camera
    cfg.set("camera.device", dev)

    def open_cam():
        print("  -> BAT camera %s" % dev, flush=True)
        return Camera("v4l2", cfg)

    return open_cam, None


# ---------------------------------------------------------------------------
def start_stdin_forwarder(ch):
    """Che do mo phong: go !CUP 1 / !BTN 3 / !VOICE 5 tren ban phim de gia lap
    cam bien va nut bam (tien loi khi sua loi ma khong co ESP32 ben canh)."""
    import threading

    def reader():
        print("(mo phong) go: !CUP 1|0 | !BTN 1..5 [L] | !BTN 0 | !VOICE 0..6", flush=True)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            ch.control(line[1:] if line.startswith("!") else line)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    return t


def run_monitor(ch, seconds=None):
    """Che do chi xem khung tin - rat tien khi go loi phan cung."""
    print("Dang nghe UART... (Ctrl+C de dung)")
    ch.send("HELLO", "1")
    t0 = time.time()
    try:
        while True:
            for m in ch.poll():
                print("  %8.2f s  %s" % (time.time() - t0, m.raw.strip()))
            ch.beat()
            time.sleep(0.02)
            if seconds and time.time() - t0 > seconds:
                break
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="May tinh dieu phoi ESP32 qua UART")
    ap.add_argument("--port", default=None, help="cong UART that (COM5, /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=None)
    ap.add_argument("--sim", action="store_true", help="dung ESP32 ao (C++ neu co, khong thi Python)")
    ap.add_argument("--pysim", action="store_true", help="ep dung ESP32 ao viet bang Python")
    ap.add_argument("--camera", default="0")
    ap.add_argument("--video", default=None)
    ap.add_argument("--synthetic", action="store_true", help="anh coc gia lap (khong can webcam)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-window", action="store_true", help="khong mo cua so OpenCV")
    ap.add_argument("--monitor", action="store_true", help="chi in khung tin UART roi thoat")
    ap.add_argument("--seconds", type=float, default=None, help="tu dong thoat sau N giay")
    ap.add_argument("--auto-test", action="store_true",
                    help="tu chay kich ban: dat coc -> nhan muc 3 -> rot -> nha coc")
    args = ap.parse_args()

    if args.port:
        pass
    elif args.sim or args.pysim or args.synthetic or args.auto_test:
        args.port = None
    else:
        ap.error("can --port COM5 (phan cung that) hoac --sim / --pysim / --synthetic")

    cfg = load_config(args.config)
    baud = args.baud or int(cfg.get("esp32.baud", 115200))
    args.baud = baud
    # Cong cu nay LUON dieu khien bom qua ESP32: dat kieu bom TRUOC khi mo bom.
    cfg.set("control.pump.type", "uart")

    link, _ = build_link(args, cfg)
    ch = EspChannel(link, hb_period_s=float(cfg.get("esp32.hb_period_s", 0.25)))

    if args.monitor:
        if not args.port:
            start_stdin_forwarder(ch)
        run_monitor(ch, args.seconds)
        ch.close()
        return 0

    # --- camera + bom + bo dieu phoi ---
    open_cam, scene = build_camera_factory(cfg, args)
    pump = open_pump(cfg, scene, channel=ch)     # -> UartRelayPump (khong giu GPIO)
    assert hasattr(pump, "last_duty"), "bom phai la UartRelayPump, dang la %r" % type(pump)
    det = CupDetector(cfg)
    app = HostApp(cfg, ch, open_cam, pump, detector=det, sim_scene=scene)
    rig = SimRig(ch, scene)      # chi dung o che do mo phong

    cv2 = None
    if not args.no_window:
        import cv2 as _cv2

        cv2 = _cv2

    fps = float(cfg.get("control.loop.fps", 15))
    dt = 1.0 / fps
    print("San sang. Cho ESP32 bao co coc ($CUP,1)...", flush=True)
    t_end = time.time() + args.seconds if args.seconds else None
    script_t = time.time() if args.auto_test else None
    script_step = 0

    while True:
        t0 = time.time()
        tel = app.tick(dt)

        # ---- kich ban tu dong (khong can ban phim) ----
        if args.auto_test and script_t is not None:
            el = time.time() - script_t
            if script_step == 0 and el > 0.5:
                print("[auto] dat coc len cam bien", flush=True)
                rig.cup(True)
                script_step = 1
            elif script_step == 2 and app.state.value == "ARMED":
                print("[auto] nhan nut muc 3 (200 ml)", flush=True)
                rig.button(3)
                script_step = 3
            elif script_step == 4 and app.state.value == "ARMED" and app.last_report:
                print("[auto] nha coc ra", flush=True)
                rig.cup(False)
                script_step = 5
            elif script_step == 5 and el > 2:
                break
            if script_step == 1 and app.state.value == "ARMED":
                script_step = 2
            if script_step == 3 and app.state.value == "ARMED" and app.last_report:
                script_step = 4

        # ---- ve ----
        if cv2 is not None:
            frame = app.ctl.frame
            if frame is not None:
                vis = draw_overlay(
                    frame,
                    app.ctl.last_det,
                    target_mm=tel.get("target_mm") or None,
                    text=[
                        "host %s | esp %s | cam %s"
                        % (tel["host_state"], tel["esp"]["state"],
                           "ON" if tel["camera_open"] else "OFF"),
                        "[%s] preset %g ml  duty %d%%"
                        % (tel["state"], tel["preset_ml"], tel["esp"]["duty"]),
                        "nuoc %.1f/%.1f mm  ~%.0f ml" % (tel["h_mm"], tel["target_mm"], tel["volume_ml"]),
                        tel["alert"] or tel["last_error"] or "",
                    ],
                )
                cv2.imshow("may_rot_nuoc <> ESP32", vis)
            key = cv2.waitKey(5) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("c"):
                cup = not tel["esp"]["cup"]
                print("  [mo phong] cam bien coc = %d" % cup, flush=True)
                rig.cup(cup)
            elif key == ord("x"):
                rig.stop()
            elif key == ord("v"):
                rig.voice(5)
            elif key == ord("s"):
                app._start_fill(int(app.ctl.preset_ml))
            elif key == ord("e"):
                app.stop_fill()
            elif ord("1") <= key <= ord("5"):
                i = key - ord("1")
                if i < len(app.presets):
                    print("  [mo phong] nhan nut muc %d (%d ml)" % (i + 1, app.presets[i]), flush=True)
                    rig.button(i + 1)
        else:
            time.sleep(dt)

        if t_end and time.time() > t_end:
            break

    print("\n--- ket thuc ---")
    print("host:", app.state.value, "| esp:", app.ctl.state.value,
          "| bao cao:", app.last_report or "(chua rot lan nao)")
    print("UART: gui %d / nhan %d / loi khung %d"
          % (ch.tx_count, ch.rx_count, ch.link_errors))
    pump.close()
    app.slot.release()
    ch.close()
    if cv2 is not None:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
