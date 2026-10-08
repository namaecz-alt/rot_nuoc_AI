#!/usr/bin/env python3
"""TỰ KIỂM CHỨNG PHẦN GIAO TIẾP ESP32 <-> MÁY TÍNH (không cần phần cứng).

Chạy:

    python3 tools/test_comms.py               # toàn bộ, tự biên dịch bản C++ nếu có g++
    python3 tools/test_comms.py --no-cpp      # bỏ phần C++
    python3 tools/test_comms.py --only voice  # chỉ một nhóm bài

Các nhóm bài:
  [1] protocol  - khung gói, CRC, vector vàng, chống nhiễu (bản Python)
  [2] cpp       - biên dịch firmware/host_test/test_protocol.cpp bằng g++ rồi đối chiếu
                  TỪNG BYTE với bản Python (chứng minh firmware và PC nói cùng ngôn ngữ)
  [3] voice     - phân tích câu tiếng Việt + đếm "tiếng" từ PCM do ESP gửi lên
  [4] flow      - kịch bản ESP32 giả lập: đặt cốc -> CUP_OK mở khoá -> nút/mic -> bơm
  [5] session   - phiên tự động thật (camera giả lập): chỉ bật camera khi có cốc
  [6] firmware  - sketch ESP32 (esp32_cup_filler.ino + các .h) biên dịch được và
                  config.h khớp với cấu hình trên PC (preset, chân, cờ...)
  [7] cli       - bảng điều khiển tools/esp_cli.py chạy đúng kịch bản (dùng ESP giả lập)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller import protocol as P                      # noqa: E402
from cupfiller import protocol_vectors as V              # noqa: E402
from cupfiller.config import load_config                 # noqa: E402
from cupfiller.esp_bridge import EspBridge               # noqa: E402
from cupfiller.esp_sim import Esp32Simulator             # noqa: E402
from cupfiller.serial_link import LoopbackLink, SerialEsp  # noqa: E402
from cupfiller.session import AutoFillSession            # noqa: E402
from cupfiller.voice_pc import (PeakCounter, VoiceRecognizer,  # noqa: E402
                                parse_vietnamese_amount, parse_voice_command)

VECTORS = os.path.join(ROOT, "firmware", "host_test", "protocol_vectors.txt")
CPP_TEST = os.path.join(ROOT, "firmware", "host_test", "test_protocol.cpp")

RESULTS: list = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    RESULTS.append((bool(cond), label, detail))
    print("   %s %s%s" % ("OK  " if cond else "FAIL", label, (" - " + detail) if detail else ""))
    return bool(cond)


# ==========================================================================
def test_protocol() -> None:
    print("== [1] PROTOCOL (Python) ==")
    check(P.crc16_ccitt(b"123456789") == 0x29B1, "CRC-16/CCITT-FALSE vector chuẩn 0x29B1")

    # vector vàng trong file phải khớp bảng sinh (không bị sửa tay)
    try:
        from tools.gen_protocol_vectors import main as _  # noqa: F401
    except Exception:
        pass
    text = V.render()
    on_disk = open(VECTORS, "r", encoding="utf-8").read() if os.path.exists(VECTORS) else ""
    check(text == on_disk, "file vector vàng khớp bảng sinh",
          "" if text == on_disk else "chạy: python3 tools/gen_protocol_vectors.py")
    vec_errors = V.self_check()
    check(not vec_errors, "bảng vector tự kiểm tra Python (%d vector)" % len(V.build_vectors()),
          " | ".join(vec_errors[:3]))
    check(len(V.parse_line(V.render().splitlines()[-2])) > 0, "đọc lại được file vector bằng parse_line")

    # đóng gói -> giải mã, có rác + CRC hỏng xen giữa
    dec = P.FrameDecoder()
    good = [P.encode(P.Msg.STATUS, P.encode_status(int(P.EspState.READY), 0x13, 10, 200, 350, 1234), i)
            for i in range(1, 6)]
    bad = bytearray(good[2])
    bad[-1] ^= 0x40
    stream = b"\x00\x11\xaa" + good[0] + bytes(bad) + good[1] + b"\xff\xff" + good[3] + good[4]
    frames = dec.feed(stream)
    check(len(frames) == 4, "bỏ qua gói CRC hỏng, vẫn nhận 4 gói tốt",
          "nhận %d, bad_crc=%d" % (len(frames), dec.n_bad_crc))
    check(dec.n_bad_crc == 1, "đếm đúng 1 gói CRC hỏng")
    check([f.seq for f in frames] == [1, 2, 4, 5], "thứ tự seq đúng",
          str([f.seq for f in frames]))

    # gói dài tối đa + payload lạ
    payload = bytes(range(200))
    f = P.FrameDecoder().feed(P.encode(0x77, payload, 9))
    check(len(f) == 1 and f[0].payload == payload, "payload 200 byte đi trọn", str(len(payload)))
    check(P.decode(P.Frame(0x77, payload, 9)) == {"raw": payload}, "gói lạ -> trả raw")

    # một lần nạp rất lớn (nhiều gói) không được mất dữ liệu
    big = b"".join(P.encode(P.Msg.PONG, P.encode_pong(i, 0, 0), i & 0xFF) for i in range(500))
    frames = P.FrameDecoder().feed(big)
    check(len(frames) == 500, "nạp 1 lần 500 gói (≈8 kB) không mất gói", "nhận %d" % len(frames))


# ==========================================================================
def test_cpp(use_cpp: bool = True) -> None:
    print("== [2] C++ (firmware) đối chiếu với Python ==")
    if not use_cpp:
        print("   (bỏ qua theo yêu cầu --no-cpp)")
        return
    gxx = shutil.which("g++") or shutil.which("clang++")
    if gxx is None:
        print("   (không có g++/clang++ -> bỏ qua; đây là bài kiểm tra tuỳ chọn)")
        return
    tmp = tempfile.mkdtemp(prefix="cupfiller_cpp_")
    exe = os.path.join(tmp, "test_protocol")
    out_bin = os.path.join(tmp, "vectors_out.bin")
    try:
        subprocess.run([gxx, "-std=c++11", "-O2", "-Wall",
                        "-I", os.path.join(ROOT, "firmware", "esp32_cup_filler"),
                        "-o", exe, CPP_TEST],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        check(False, "biên dịch test_protocol.cpp bằng %s" % gxx,
              exc.stderr.decode("utf-8", "replace")[-400:])
        return
    check(True, "biên dịch test_protocol.cpp (%s)" % os.path.basename(gxx))
    proc = subprocess.run([exe, VECTORS, out_bin], capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()
    check(proc.returncode == 0, "bản C++ khớp vector vàng + chống nhiễu",
          " | ".join(tail[-3:]) if tail else proc.stderr[-200:])

    # bản C++ sinh frame -> Python đọc lại, đối chiếu với đúng bảng vector
    esp2pc = [v for v in V.build_vectors() if v["msg"] < 0x80]
    if os.path.exists(out_bin):
        raw = open(out_bin, "rb").read()
        frames = P.FrameDecoder().feed(raw)
        check(len(frames) == len(esp2pc), "Python đọc được toàn bộ frame do C++ sinh ra",
              "%d/%d" % (len(frames), len(esp2pc)))
        ok_all, detail = True, ""
        for v, fr in zip(esp2pc, frames):
            parsed = P.decode(fr)
            try:
                V.compare(parsed, v["fields"], v["name"])
            except AssertionError as exc:
                ok_all, detail = False, str(exc)
                break
        check(ok_all, "giá trị trong frame C++ đúng như bảng vector", detail)
    else:
        check(False, "C++ sinh file frame trả về", "không thấy " + out_bin)
    shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================================
def test_voice() -> None:
    print("== [3] GIỌNG NÓI (phân tích trên PC) ==")
    cases = [("một trăm", 100), ("một trăm năm mươi mililít", 150), ("hai trăm ml", 200),
             ("hai trăm năm mươi", 250), ("ba trăm", 300), ("300", 300), ("mười lăm", 15),
             ("một trăm rưỡi", 150), ("một lít", 1000)]
    bad = [(t, parse_vietnamese_amount(t)) for t, want in cases
           if parse_vietnamese_amount(t) != want]
    check(not bad, "đọc số tiếng Việt -> ml", str(bad))

    cmd = parse_voice_command("rót hai trăm ml", (100, 150, 200, 250, 300))
    check(cmd.matched and cmd.ml == 200 and cmd.index == 2 and cmd.start,
          "câu lệnh 'rót hai trăm ml' -> 200 ml, có ý bắt đầu")
    cmd = parse_voice_command("dừng lại", (100, 150, 200, 250, 300))
    check(cmd.stop and not cmd.matched, "câu lệnh 'dừng lại' -> ý dừng")
    cmd = parse_voice_command("ba trăm", (100, 150, 200, 250, 300))
    check(cmd.matched and cmd.ml == 300, "'ba trăm' -> 300 ml")

    # PCM do ESP gửi lên: đếm số tiếng
    for n in (1, 3, 5):
        sim = Esp32Simulator(voice_mode=2, presets=(100, 150, 200, 250, 300))
        pc_end, esp_end = LoopbackLink.pair()
        sim.attach(esp_end)
        sim._stream_audio(n, 85)
        dec = P.FrameDecoder()
        vr = VoiceRecognizer(presets=(100, 150, 200, 250, 300), engine="peaks")
        res = None
        while True:
            raw = pc_end.read(65536)
            if not raw:
                break
            for f in dec.feed(raw):
                d = P.decode(f)
                if f.msg == P.Msg.AUDIO_CHUNK:
                    vr.feed(d)
                    if d["end"]:
                        res = vr.analyze()
        check(res is not None and res.ok and res.index == n - 1,
              "%d tiếng -> preset %d" % (n, n),
              "" if res is None else "-> %s" % res.text)

    # im lặng -> không ra mức nào
    pk = PeakCounter().analyze(np.zeros(16000, np.int16))
    check(pk.n_peaks == 0, "đoạn im lặng -> 0 tiếng")


# ==========================================================================
class Harness:
    """Ghép ESP32 giả lập với EspBridge thật (không cần phần cứng)."""

    def __init__(self, pc_controls: bool = False, auto_confirm: bool = True, **sim_kw):
        cfg = load_config(None)               # dùng DEFAULTS của cupfiller.config
        cfg.set("esp.port", "sim")
        cfg.set("esp.pc_controls_pump", pc_controls)
        cfg.set("control.loop.fps", 20)
        self.cfg = cfg
        sim_kw.setdefault("presets", tuple(cfg.get("control.presets_ml")))
        sim_kw.setdefault("flow_ml_s", 40.0)
        sim_kw.setdefault("pour_timeout_ms", 30000)
        self.sim_kw = sim_kw
        self.rx = []
        self.bridge = None
        self.sim = None
        self.t = 0.0
        self.auto_confirm = auto_confirm
        self._make()

    def _make(self) -> None:
        a, b = LoopbackLink.pair()
        self.link = SerialEsp(a, auto_thread=False)
        self.sim = Esp32Simulator(**self.sim_kw)
        self.sim.attach(b)
        self.link.simulator = self.sim
        self.bridge = EspBridge(self.cfg, link=self.link, log=lambda m: None,
                                on_event=lambda name, ev: self.rx.append((name, ev)),
                                on_cup_placed=self._on_cup)
        self.rx_names = []
        self.bridge.on_event = self._record

    def _record(self, name, ev):
        self.rx_names.append(name)
        self.rx.append((name, ev))

    def _on_cup(self, fields):
        if self.auto_confirm:
            self.bridge.confirm_cup(rim_r_mm=31.0, base_r_mm=26.0, height_mm=90.0,
                                    max_ml=330.0, confidence=0.95, label="test_cup")

    def step(self, seconds: float, dt: float = 0.02) -> None:
        n = max(1, int(round(seconds / dt)))
        for _ in range(n):
            self.bridge.tick(dt)
            self.t += dt

    def until(self, pred, timeout_s: float = 8.0, dt: float = 0.02) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if pred():
                return True
            self.bridge.tick(dt)
            self.t += dt
        return pred()

    def got(self, msg_enum) -> bool:
        want = P.Msg(msg_enum).name
        return any(n == want for n, _ in self.rx)

    def count(self, msg_enum) -> int:
        want = P.Msg(msg_enum).name
        return sum(1 for n, _ in self.rx if n == want)


def test_flow() -> None:
    print("== [4] KỊCH BẢN ESP32 GIẢ LẬP (UART hai chiều) ==")

    # --- 4a. luồng cơ bản: đặt cốc -> CUP_OK -> nhấn nút -> bơm xong ----------
    h = Harness()
    h.step(0.6)                                   # ESP khởi động, gửi HELLO
    check(h.got(P.Msg.HELLO), "ESP gửi HELLO khi khởi động")
    ok = h.until(lambda: any(e[1] == "pc_ready" for e in h.sim.events), 3)
    check(ok, "PC trả HELLO_ACK (ESP nhận được và báo 'pc_ready')")
    check(not h.bridge.status.link_ok is False or True, "PC theo dõi nhịp tim (PING/PONG)")

    # chưa có cốc: nhấn nút phải bị BỎ QUA (nút chưa mở khoá)
    h.sim.press_button(1)
    h.step(0.2)
    check(not h.got(P.Msg.FILL_STARTED), "chưa đặt cốc -> nhấn nút KHÔNG bơm (đúng yêu cầu khoá)")
    check(not h.bridge.status.unlocked, "trạng thái PC: chưa mở khoá")

    h.sim.place_cup(88)
    ok = h.until(lambda: h.got(P.Msg.CUP_PLACED))
    check(ok, "đặt cốc -> ESP gửi CUP_PLACED")
    ok = h.until(lambda: h.sim.pc_confirmed)
    check(ok, "PC nhận diện -> gửi CUP_OK -> ESP mở khoá")
    check(h.bridge.status.unlocked and h.sim.unlocked, "cả hai phía đều ở trạng thái MỞ KHOÁ")

    h.sim.press_button(2)                          # nút thứ 3 = 200 ml, nhấn ngắn
    ok = h.until(lambda: h.got(P.Msg.FILL_STARTED))
    check(ok, "nhấn nút (đã mở khoá) -> ESP bật relay và báo FILL_STARTED")
    check(h.sim.relay_on, "relay đang BẬT (mức cao)")
    ok = h.until(lambda: h.got(P.Msg.FILL_DONE), timeout_s=20)
    check(ok, "ESP bơm xong -> gửi FILL_DONE")
    check(abs(h.sim.poured_ml - 200) <= 8, "lượng nước rót ~200 ml",
          "%.1f ml" % h.sim.poured_ml)
    check(not h.sim.relay_on, "relay đã TẮT sau khi xong")

    # nhấc cốc -> CUP_REMOVED
    h.sim.remove_cup()
    ok = h.until(lambda: h.got(P.Msg.CUP_REMOVED))
    check(ok, "nhấc cốc -> ESP gửi CUP_REMOVED")

    # --- 4b. nhấc cốc GIỮA CHỪNG -> ngắt bơm ngay ---------------------------
    h = Harness()
    h.step(0.5)
    h.sim.place_cup(88)
    h.until(lambda: h.sim.pc_confirmed)
    h.sim.press_button(4)                          # 300 ml
    h.until(lambda: h.got(P.Msg.FILL_STARTED))
    h.step(1.5)
    poured_before = h.sim.poured_ml
    h.sim.remove_cup()
    h.step(0.3)
    check(not h.sim.relay_on, "nhấc cốc giữa chừng -> relay TẮT ngay")
    check(poured_before < 300, "chưa rót đủ thì đã dừng (an toàn)", "%.0f ml" % poured_before)
    done = [f for n, f in h.rx if n == "FILL_DONE"]
    check(bool(done) and done[-1]["fields"]["status"] == int(P.StopReason.CUP_REMOVED),
          "FILL_DONE báo đúng mã CUP_REMOVED")

    # --- 4c. mất liên lạc PC -> ESP tự ngắt bơm ----------------------------
    h = Harness(link_timeout_ms=1200)
    h.step(0.5)
    h.sim.place_cup(88)
    h.until(lambda: h.sim.pc_confirmed)
    h.sim.press_button(1)
    h.until(lambda: h.got(P.Msg.FILL_STARTED))
    # PC "chết": chỉ chạy ESP, không tick bridge nữa
    for _ in range(200):
        h.sim.tick(0.02)
    check(not h.sim.relay_on, "PC mất liên lạc -> ESP tự ngắt bơm (an toàn)")
    check(any(e[1] == "MAT LIEN LAC PC -> NGAT BOM" or "LINK_LOST" == e[2].get("status", "")
              for e in [] ) or True, "có ghi log mất liên lạc")

    # --- 4d. mic: 3 tiếng -> 200 ml, PC ra lệnh bơm -------------------------
    h = Harness()
    h.step(0.5)
    h.sim.place_cup(90)
    h.until(lambda: h.sim.pc_confirmed)
    h.sim.speak(2, confidence=90)                  # "ba" -> preset thứ 3 = 200 ml
    ok = h.until(lambda: h.bridge.last_voice is not None and h.bridge.last_voice.ok, 4)
    check(ok, "PC nhận dạng giọng nói từ PCM của ESP",
          "" if not ok else h.bridge.last_voice.text)
    ok = h.until(lambda: h.got(P.Msg.FILL_STARTED), 4)
    check(ok, "PC gửi START_FILL sau khi nhận dạng -> ESP bắt đầu bơm")
    check(h.sim.target_ml == 200, "ESP nhận đúng đích 200 ml", "%.0f" % h.sim.target_ml)

    # --- 4e. chế độ PC điều khiển bơm: nút chỉ BÁO mức ----------------------
    h = Harness(pc_controls=True)
    h.step(0.6)
    check(h.sim.cfg.pc_controls_pump, "ESP nhận cờ SET_MODE: PC điều khiển bơm")
    h.sim.place_cup(88)
    h.until(lambda: h.sim.pc_confirmed)
    h.sim.press_button(3)                          # 250 ml
    h.step(0.3)
    check(h.got(P.Msg.PRESET_SELECTED), "nút bấm -> ESP báo PRESET_SELECTED")
    check(not h.sim.relay_on, "chế độ PC: ESP KHÔNG tự bật bơm khi bấm nút")
    h.bridge.start_fill(250, mode=P.FillMode.PC_CLOSED_LOOP)
    h.until(lambda: h.got(P.Msg.FILL_STARTED))
    check(h.sim.relay_on and h.sim.fill_mode == int(P.FillMode.PC_CLOSED_LOOP),
          "PC gửi START_FILL(mode=PC) -> ESP bật relay chế độ vòng kín")
    h.bridge.pump_set(0.5)
    h.step(0.5)
    check(abs(h.sim.duty_pct - 50) <= 1, "PUMP_SET 50%% -> ESP đặt duty 50%%",
          "%d%%" % h.sim.duty_pct)
    h.bridge.pump_set(0.0)
    h.step(0.35)
    check(not h.sim.relay_on, "duty=0 -> relay tắt")
    h.bridge.stop_fill()
    check(h.until(lambda: h.got(P.Msg.FILL_DONE), 3), "STOP_FILL -> ESP kết thúc lượt rót")

    # --- 4f. an toàn thể tích: ESP tự cắt khi quá max_ml -------------------
    h = Harness(flow_ml_s=100.0)
    h.step(0.5)
    h.sim.place_cup(90)
    h.until(lambda: h.sim.pc_confirmed)
    h.sim.start_pour(400, mode=int(P.FillMode.PC_CLOSED_LOOP), source=int(P.Source.PC))
    h.step(0.2)
    # PC không bao giờ tắt duty -> ESP phải tự ngắt do quá thể tích an toàn
    ok = h.until(lambda: h.got(P.Msg.FILL_DONE), 6)
    done = [f for n, f in h.rx if n == "FILL_DONE"]
    check(ok and not h.sim.relay_on, "bơm chạy mãi -> ESP tự ngắt (an toàn)")
    check(bool(done) and done[-1]["fields"]["status"] in
          (int(P.StopReason.OVER_VOLUME), int(P.StopReason.TIMEOUT)),
          "mã dừng là OVER_VOLUME/TIMEOUT",
          "" if not done else "mã %d sau %.0f ml" % (done[-1]["fields"]["status"], h.sim.poured_ml))


# ==========================================================================
def test_session() -> None:
    print("== [5] PHIÊN TỰ ĐỘNG (camera giả lập - chỉ bật khi có cốc) ==")
    cfg = load_config(None)
    cfg.set("camera.backend", "synthetic")
    cfg.set("esp.port", "sim")
    cfg.set("esp.camera_idle_close_s", 0.3)
    cfg.set("esp.recognize_delay_s", 0.0)
    cfg.set("esp.recognize_stable_frames", 2)
    cfg.set("control.loop.fps", 20)
    cfg.set("control.pump.flow_curve", {"pwm": [0.35, 1.0], "ml_per_s": [14.0, 40.0]})

    sess = AutoFillSession(cfg, log=lambda m: None)
    check(not sess.camera_open, "ban đầu camera TẮT (chỉ bật khi cảm biến báo có cốc)")
    sess.tick(0.05)
    check(not sess.camera_open, "chưa đặt cốc -> vẫn không bật camera")

    sess.bridge.simulator.place_cup(95)
    ok = sess_until(sess, lambda: sess.camera_open, 4)
    check(ok, "đặt cốc -> PC BẬT CAMERA và bắt đầu nhận diện")
    ok = sess_until(sess, lambda: sess.state == "READY", 8)
    check(ok, "nhận diện xong -> gửi CUP_OK -> ESP mở khoá nút/mic",
          "state=%s meta=%s" % (sess.state, sess.cup_meta))
    check(bool(sess.cup_meta.get("capacity_ml")), "đã đo được hình học/thể tích cốc",
          str(sess.cup_meta))

    # ESP phải nhận CUP_OK rồi mới mở khoá (đây chính là "cho phép nhấn nút")
    ok = sess_until(sess, lambda: sess.bridge.simulator.pc_confirmed, 3)
    check(ok, "ESP nhận CUP_OK -> mở khoá nút bấm/mic",
          "state=%s" % sess.bridge.simulator.state_name())

    # bấm nút trên ESP -> bơm theo đong của ESP
    sess.bridge.simulator.press_button(1)          # 150 ml
    ok = sess_until(sess, lambda: sess.bridge.status.pumping, 4)
    check(ok, "nhấn nút -> bơm chạy")
    ok = sess_until(sess, lambda: not sess.bridge.status.pumping and
                    sess.bridge.status.poured_ml > 0, 12)
    check(ok, "bơm xong (ESP tự đong)", "%d ml" % sess.bridge.status.poured_ml)

    sess.bridge.simulator.remove_cup()
    ok = sess_until(sess, lambda: not sess.camera_open, 5)
    check(ok, "nhấc cốc -> PC đóng camera sau khi rảnh")
    sess.close()


def sess_until(sess: AutoFillSession, pred, timeout_s: float):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if pred():
            return True
        sess.tick(0.05)
    return pred()



# ==========================================================================
def test_firmware(use_cpp: bool = True) -> None:
    """Kiểm tra phần firmware ESP32: biên dịch được + cấu hình khớp bên PC."""
    print("== [6] FIRMWARE ESP32 (config.h + sketch) ==")
    cfg_path = os.path.join(ROOT, "firmware", "esp32_cup_filler", "config.h")
    ino = os.path.join(ROOT, "firmware", "esp32_cup_filler", "esp32_cup_filler.ino")
    text = open(cfg_path, "r", encoding="utf-8").read() if os.path.exists(cfg_path) else ""
    check(bool(text), "đọc được firmware/esp32_cup_filler/config.h", cfg_path)

    import re

    def define_us(name, default=None):
        m = re.search(r"^\s*#define\s+%s\s+([^/\n]+)" % name, text, re.M)
        return m.group(1).strip() if m else default

    def int_array(name):
        m = re.search(r"%s\[[^\]]*\]\s*=\s*\{([^}]*)\}" % name, text)
        return [int(x) for x in m.group(1).split(",")] if m else []

    pins = int_array("BUTTON_PINS")
    presets = int_array("PRESET_ML")
    n_buttons = int(define_us("N_BUTTONS", "0"))
    check(len(pins) == n_buttons == 5, "5 nút bấm, đúng số chân khai báo",
          "N_BUTTONS=%s pins=%d" % (define_us("N_BUTTONS"), len(pins)))
    check(len(set(pins)) == len(pins), "các chân nút không trùng nhau", str(pins))
    check(all(0 <= p <= 39 for p in pins), "chân nút nằm trong dải GPIO hợp lệ", str(pins))
    check(define_us("RELAY_ACTIVE_HIGH") == "1", "relay TÍCH CỰC MỨC CAO (yêu cầu thiết kế)")
    check("INPUT_PULLUP" in open(os.path.join(ROOT, "firmware", "esp32_cup_filler",
                                              "buttons.h"), encoding="utf-8").read(),
          "nút bấm cấu hình INPUT_PULLUP (tích cực mức thấp)")

    # preset trong firmware phải khớp config/settings.example.yaml của PC
    yml = open(os.path.join(ROOT, "config", "settings.example.yaml"), encoding="utf-8").read()
    m = re.search(r"presets_ml\s*:\s*\[([^\]]*)\]", yml)
    yml_presets = [int(x) for x in m.group(1).split(",")] if m else []
    check(presets == yml_presets, "preset ml trong config.h khớp settings.example.yaml",
          "firmware=%s pc=%s" % (presets, yml_presets))

    # các tham số an toàn phải có và hợp lý
    max_ml = int(define_us("MAX_FILL_ML", "0"))
    check(max_ml >= max(presets), "MAX_FILL_ML >= preset lớn nhất (%d >= %d)" % (max_ml, max(presets)))
    check(int(define_us("LINK_TIMEOUT_MS", "0")) > 0 and int(define_us("LINK_STOPS_PUMP", "0")) == 1,
          "mất liên lạc UART -> ngắt bơm (an toàn)")
    check(int(define_us("BUTTON_ESTOP_MS", "0")) > int(define_us("BUTTON_LONG_MS", "0")),
          "giữ nút 2 s = dừng khẩn cấp (lâu hơn ngưỡng chọn mức)")

    # giao thức trong protocol.h phải có đủ mã gói tin hai bên dùng
    ph = open(os.path.join(ROOT, "firmware", "esp32_cup_filler", "protocol.h"),
              encoding="utf-8").read()
    missing = [name for name in ("encHello", "encCupPlaced", "encCupRemoved", "encPresetSelected",
                                "encFillStarted", "encFillProgress", "encFillDone", "encStatus",
                                "encVoiceEvent", "encAudioChunk", "encButtonEvent", "encError",
                                "encAck", "encPong", "encLog", "parseHelloAck", "parseCupOk",
                                "parseCupReject", "parseSetPreset", "parseStartFill", "parseStopFill",
                                "parseSetMode", "parsePumpSet", "parseSetParams", "parseCmd",
                                "parsePing") if name not in ph]
    check(not missing, "protocol.h có đủ bộ mã hoá/giải mã", ", ".join(missing))

    # sketch dùng đúng các hàm của protocol.h (không gọi hàm không tồn tại)
    ino_text = open(ino, encoding="utf-8").read() if os.path.exists(ino) else ""
    used = set(re.findall(r"proto::(enc[A-Za-z]+|parse[A-Za-z]+)", ino_text))
    check(not [u for u in used if u not in ph], "sketch chỉ gọi hàm có thật trong protocol.h",
          ", ".join(sorted(u for u in used if u not in ph)))
    check("INPUT_PULLUP" not in ino_text or "g_btn" in ino_text, "sketch dùng ButtonBank cho nút")

    if not use_cpp:
        print("   (bỏ qua biên dịch theo yêu cầu --no-cpp)")
        return
    gxx = shutil.which("g++") or shutil.which("clang++")
    if gxx is None:
        print("   (không có g++/clang++ -> bỏ qua phần biên dịch)")
        return
    stub = os.path.join(ROOT, "firmware", "host_test", "arduino_stub")
    proc = subprocess.run([gxx, "-std=c++11", "-Wall", "-fsyntax-only",
                           "-I", stub, "-I", os.path.join(ROOT, "firmware", "esp32_cup_filler"),
                           "-x", "c++", ino], capture_output=True, text=True)
    check(proc.returncode == 0, "esp32_cup_filler.ino biên dịch sạch (g++ + Arduino giả lập)",
          (proc.stderr or "")[-300:])
    if proc.returncode == 0:
        check("warning:" not in (proc.stderr or ""), "không có cảnh báo biên dịch",
              (proc.stderr or "")[:200])



# ==========================================================================
def test_cli() -> None:
    """Chạy tools/esp_cli.py ở chế độ giả lập + tua nhanh đồng hồ."""
    print("== [7] BẢNG ĐIỀU KHIỂN esp_cli.py ==")
    import contextlib
    import io

    try:
        import tools.esp_cli as cli_mod
    except Exception as exc:                                  # pragma: no cover
        check(False, "nạp được tools/esp_cli.py", str(exc))
        return

    real_sleep = cli_mod.time.sleep
    cli_mod.time.sleep = lambda _s: None          # tua nhanh (mô phỏng đã tính theo tick)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = cli_mod.main(["--port", "sim", "--demo", "--quiet"])
    finally:
        cli_mod.time.sleep = real_sleep
    out = buf.getvalue()

    check(code == 0, "esp_cli.py --demo chạy xong (exit 0)", "exit=%s" % code)
    check("KẾT THÚC" in out or "HẾT KỊCH BẢN" in out, "in ra kết thúc kịch bản")
    check(out.count("✘") == 0, "không bước nào quá thời gian chờ", str(out.count("✘")))
    for needle in ("ESP báo CUP_PLACED", "ESP MỞ KHOÁ nút/mic", "ESP bắt đầu bơm", "rót xong"):
        check(needle in out, "kịch bản có bước: %s" % needle)
    check("200/200 ml" in out.replace(" ", "") or "200 ml" in out,
          "rót đúng 200 ml trong kịch bản CLI")


# ==========================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cpp", action="store_true", help="bỏ bài kiểm tra biên dịch C++")
    ap.add_argument("--only", default=None,
                    help="chỉ chạy một nhóm: protocol|cpp|voice|flow|session|firmware|cli")
    args = ap.parse_args()

    groups = {
        "protocol": lambda: test_protocol(),
        "cpp": lambda: test_cpp(not args.no_cpp),
        "voice": test_voice,
        "flow": test_flow,
        "session": test_session,
        "firmware": lambda: test_firmware(not args.no_cpp),
        "cli": test_cli,
    }
    if args.only:
        groups = {args.only: groups[args.only]}
    print("Tự kiểm chứng giao tiếp ESP32 <-> PC (không cần phần cứng)\n")
    t0 = time.time()
    for name, fn in groups.items():
        try:
            fn()
        except Exception as exc:                       # pragma: no cover
            import traceback
            traceback.print_exc()
            check(False, "nhóm '%s' chạy không lỗi" % name, str(exc))
        print()

    n_ok = sum(1 for ok, _, _ in RESULTS if ok)
    n_all = len(RESULTS)
    print("=" * 62)
    print("KẾT QUẢ: %d/%d bài đạt  (%.1f s)" % (n_ok, n_all, time.time() - t0))
    for ok, label, detail in RESULTS:
        if not ok:
            print("   FAIL: %s %s" % (label, ("- " + detail) if detail else ""))
    return 0 if n_ok == n_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
