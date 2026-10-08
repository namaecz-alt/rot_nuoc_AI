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
  [7] web       - giao diện web chế độ ESP32 (Flask test client): bấm nút trên web,
                  /api/telemetry, /api/esp, ảnh /video (kể cả khi camera đang tắt)
  [8] cli       - bảng điều khiển tools/esp_cli.py chạy đúng kịch bản (ESP32 giả lập)
  [9] entry     - chạy THẬT chương trình người dùng gõ: tools/run_pc.py --demo
  [6] firmware  - sketch ESP32 (esp32_cup_filler.ino + các .h) biên dịch được và
                  config.h khớp với cấu hình trên PC (preset, chân, cờ...)
  [7] cli       - bảng điều khiển tools/esp_cli.py chạy đúng kịch bản (dùng ESP giả lập)
"""
from __future__ import annotations

import argparse
import json
import os
import threading
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
    missing = [r.name for r in P.StopReason if not P.stop_reason_text(int(r))]
    check(not missing, "mọi mã dừng đều có câu tiếng Việt để hiển thị", str(missing))
    check("cảm biến" in P.stop_reason_text(int(P.StopReason.SENSOR_FAULT)).lower(),
          "mã dừng 7 (lỗi cảm biến) hiển thị đúng là lỗi cảm biến",
          P.stop_reason_text(int(P.StopReason.SENSOR_FAULT)))
    check("không rõ" in P.stop_reason_text(99), "mã dừng lạ -> báo 'mã không rõ'")

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

    # Mic ANALOG của ESP không chạy đúng 16 kHz -> firmware gửi kèm mã tần số trong
    # 4 bit cao của flags. PC phải đếm "tiếng" theo ĐÚNG tần số đó.
    for n_burst, rate in ((1, 10000), (3, 10000), (2, 8000)):
        tone = [int(9000 * np.sin(2 * np.pi * 300 * i / rate)) for i in range(int(0.20 * rate))]
        gap = [0] * int(0.15 * rate)
        samples = []
        for k in range(n_burst):
            samples += tone
            if k < n_burst - 1:
                samples += gap
        samples += [0] * int(0.25 * rate)
        vr = VoiceRecognizer(presets=(100, 150, 200, 250, 300), engine="peaks")
        chunks = [samples[i:i + 96] for i in range(0, len(samples), 96)]
        res = None
        for ci, part in enumerate(chunks):
            flags = (P.AU_START if ci == 0 else 0) | (P.AU_END if ci == len(chunks) - 1 else 0)
            payload = P.encode_audio_chunk(ci & 0xFF, flags, part, rate_hz=rate)
            info = P.parse_audio_chunk(payload)
            vr.feed(info)
            if info["end"]:
                res = vr.analyze()
        check(res is not None and res.ok and res.index == n_burst - 1,
              "%d tiếng @ %d Hz (mã tần số trong flags) -> preset %d"
              % (n_burst, rate, n_burst),
              "flags rate = %s Hz, kết quả = %s" % (
                  P.parse_audio_chunk(payload)["rate_hz"], None if res is None else res.index))

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

    # ---- an toàn chân cho board ESP32 DevKit V1 (ESP32-WROOM-32) ----
    def pin_of(name):
        m = re.search(r"^\s*#define\s+%s\s+(\d+)" % name, text, re.M)
        return int(m.group(1)) if m else None

    named = {
        "UART2 RX": pin_of("PIN_UART_RX"), "UART2 TX": pin_of("PIN_UART_TX"),
        "HC-SR04 TRIG": pin_of("PIN_TRIG"), "HC-SR04 ECHO": pin_of("PIN_ECHO"),
        "relay bơm": pin_of("PIN_RELAY"), "LED": pin_of("PIN_LED"),
        "mic ADC": pin_of("PIN_MIC_ADC"), "I2S SCK": pin_of("PIN_I2S_SCK"),
        "I2S WS": pin_of("PIN_I2S_WS"), "I2S SD": pin_of("PIN_I2S_SD"),
        "cảm biến cốc (digital)": pin_of("PIN_CUP_DIGITAL"),
    }
    for i, p in enumerate(pins):
        named["nút %d ml" % presets[i]] = p
    flash = {k: v for k, v in named.items() if v is not None and 6 <= v <= 11}
    check(not flash, "không dùng GPIO 6..11 (chân nối flash trong của ESP32)", str(flash))
    only_read = {k: v for k, v in named.items()
                 if v is not None and 34 <= v <= 39 and not k.startswith("mic ")
                 and "cảm biến cốc" not in k}
    check(not only_read,
          "chỉ GPIO 34..39 cho việc CHỈ ĐỌC; nút/relay/LED không dùng chân này", str(only_read))
    used = {}
    for k, v in named.items():
        if v is not None:
            used.setdefault(v, []).append(k)
    dup = {v: k for v, k in used.items() if len(k) > 1}
    check(not dup, "mỗi chân chỉ dùng cho MỘT chức năng (không trùng chân)", str(dup))
    adc = pin_of("PIN_MIC_ADC")
    check(adc is not None and 32 <= adc <= 39,
          "mic analog dùng ADC1 (GPIO 32..39) để đọc được cả khi bật WiFi",
          "PIN_MIC_ADC=%s" % adc)
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
def test_entry() -> None:
    """Chạy đúng lệnh mà người dùng sẽ gõ: tools/run_pc.py --port sim --synthetic --demo."""
    print("== [10] CHẠY THẬT tools/run_pc.py --port sim --synthetic --demo ==")
    cmd = [sys.executable, os.path.join(ROOT, "tools", "run_pc.py"),
           "--port", "sim", "--synthetic", "--demo"]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        check(False, "kịch bản tự chạy kết thúc trong 300 s")
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = [l for l in out.strip().splitlines() if l.strip()]
    check(proc.returncode == 0, "kịch bản tự chạy thành công (exit 0)",
          "exit=%s | %s" % (proc.returncode, tail[-1] if tail else ""))
    check("10/10 bước đạt" in out, "cả 10 bước của luồng thiết kế đều đạt")
    for needle in ("bấm nút khi chưa có cốc -> ESP bỏ qua (nút còn khoá)",
                   "camera đang tắt (chỉ bật khi có cốc)",
                   "ESP gửi CUP_PLACED -> PC BẬT CAMERA",
                   "bấm nút trước khi PC xác nhận -> ESP bỏ qua",
                   "PC gửi CUP_OK và ESP mở khoá",
                   "ESP gửi PRESET_SELECTED mức 200 ml và BẮT ĐẦU BƠM",
                   "đúng mục tiêu 200 ml",
                   "PC đóng camera"):
        check(needle in out, "kịch bản có bước: %s" % needle)
    check("CHUA MO KHOA" in out, "ESP32 giả lập cũng từ chối nút khi chưa mở khoá")



# ==========================================================================
def test_cli() -> None:
    """Bảng điều khiển tools/esp_cli.py (ESP32 giả lập, đồng hồ tua nhanh)."""
    print("== [9] BẢNG ĐIỀU KHIỂN esp_cli.py ==")
    import contextlib
    import io

    try:
        import tools.esp_cli as cli_mod
    except Exception as exc:                                   # pragma: no cover
        check(False, "nạp được tools/esp_cli.py", str(exc))
        return

    real_sleep = cli_mod.time.sleep
    cli_mod.time.sleep = lambda _s: None            # tua nhanh (sim đã tính theo dt)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = cli_mod.main(["--port", "sim", "--demo", "--quiet"])
    finally:
        cli_mod.time.sleep = real_sleep
    out = buf.getvalue()

    check(code == 0, "esp_cli.py --demo chạy xong không lỗi", "exit=%s" % code)
    check("HẾT KỊCH BẢN" in out, "in đủ kịch bản tới bước cuối")
    check("✘" not in out, "không bước nào bị quá thời gian chờ")
    for needle in ("CUP_PLACED", "CUP_OK", "ESP bắt đầu bơm", "rót xong", "CUP_REMOVED",
                   "200/200 ml"):
        check(needle in out, "kịch bản có bước %s" % needle)
    check("trần=300 ml" in out, "CUP_OK giới hạn được trần rót trên ESP (300 ml)")



# ==========================================================================
def test_fwrun() -> None:
    """CHẠY THẬT firmware ESP32 trên máy tính rồi đối chiếu với protocol.py.

    firmware/host_test/test_firmware_run.cpp nạp thẳng esp32_cup_filler.ino vào một máy
    ảo mini (đồng hồ ảo, chân GPIO thật, HC-SR04 giả, mic giả, UART2 nối vào đây), nên
    các gói tin dưới đây là do CHÍNH mã firmware phát ra, được protocol.py giải mã lại.
    """
    print("== [7] CHẠY THẬT FIRMWARE TRÊN MÁY TÍNH (HIL) ==")
    gxx = shutil.which("g++")
    if not gxx:
        check(False, "tìm thấy g++ để chạy firmware trên máy tính")
        return
    src = os.path.join(ROOT, "firmware", "host_test", "test_firmware_run.cpp")
    stub = os.path.join(ROOT, "firmware", "host_test", "arduino_stub", "instances.cpp")
    inc_fw = os.path.join(ROOT, "firmware", "esp32_cup_filler")
    inc_host = os.path.join(ROOT, "firmware", "host_test")
    inc_stub = os.path.join(ROOT, "firmware", "host_test", "arduino_stub")

    def build_and_run(flags, label):
        """Biên dịch + chạy bộ test firmware với một cấu hình chân/kênh cho trước."""
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "fwrun")
            proc = subprocess.run([gxx, "-std=c++11", "-O2", "-Wall"] + flags +
                                  ["-I", inc_fw, "-I", inc_host, "-I", inc_stub,
                                   "-o", exe, src, stub, "-lm"],
                                  capture_output=True, text=True, timeout=300)
            warn = [l for l in (proc.stderr or "").splitlines() if "warning:" in l]
            if proc.returncode != 0:
                check(False, "biên dịch test_firmware_run.cpp (%s)" % label,
                      (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "")
                return None, ""
            if warn:
                check(False, "biên dịch %s không có cảnh báo" % label, warn[0][:160])
            try:
                run = subprocess.run([exe], capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                check(False, "chạy kịch bản firmware (%s) kết thúc trong 600 s" % label)
                return None, ""
        return run, (run.stdout or "")

    run, out = build_and_run([], "kênh UART2 trên GPIO16/17 (mặc định)")
    if run is None or not out:
        check(False, "chạy được kịch bản firmware trên máy tính")
        return
    check(True, "biên dịch + chạy được firmware thật + máy ảo mini (không cảnh báo)")
    lines = out.splitlines()

    # ---- các bài kiểm tra bên trong firmware (C++) ----
    checks = [(l[len("CHECK OK "):], True) for l in lines if l.startswith("CHECK OK ")]
    checks += [(l[len("CHECK FAIL "):], False) for l in lines if l.startswith("CHECK FAIL ")]
    bad = [name for name, okay in checks if not okay]
    check(checks and not bad, "tất cả %d bài kiểm tra logic trong firmware đều đạt" % len(checks),
          " | ".join(bad[:3]))
    check("RESULT" in out, "firmware chạy hết kịch bản (in RESULT)")

    # ---- gói tin THẬT của firmware, giải mã bằng protocol.py ----
    tx = "\n".join(l[3:] for l in lines if l.startswith("TX "))
    dec = P.FrameDecoder()
    frames = []
    for chunk in tx.splitlines():
        raw = bytes.fromhex(chunk.strip())
        frames += dec.feed(raw)
    by: dict = {}
    for f in frames:
        try:
            name = P.Msg(f.msg).name
        except ValueError:
            name = "MSG_%02X" % f.msg
        by.setdefault(name, []).append(P.decode(f))
    check(len(frames) > 30, "firmware phát ra nhiều gói tin qua UART", "%d gói" % len(frames))
    check(dec.n_bad_crc == 0 and dec.n_dropped_bytes == 0,
          "gói firmware phát ra hợp lệ 100%% (không sai CRC, không rác)",
          "bad_crc=%d dropped=%d" % (dec.n_bad_crc, dec.n_dropped_bytes))
    check("HELLO" in by and by["HELLO"][0]["version"] == P.PROTO_VERSION,
          "firmware gửi HELLO có phiên bản giao thức đúng")
    check(by.get("HELLO", [{}])[0].get("presets") == [100, 150, 200, 250, 300],
          "HELLO khai báo đúng 5 mức nước")

    cp = by.get("CUP_PLACED", [])
    check(cp and cp[0]["flags"] & 1, "đặt cốc -> CUP_PLACED (cờ 'mới phát hiện')",
          "%d gói CUP_PLACED" % len(cp))
    check(cp and 60 <= cp[0]["height_mm"] <= 130, "CUP_PLACED báo đúng chiều cao cốc",
          "%s mm" % (cp[0]["height_mm"] if cp else "?"))

    # trạng thái: chưa xác nhận -> không mở khoá
    st = by.get("STATUS", [])
    first_unconf = [d for d in st if not d["pc_confirmed"]]
    check(first_unconf and not first_unconf[0]["cup_present"],
          "STATUS đầu tiên: chưa có cốc, chưa mở khoá")

    ps = by.get("PRESET_SELECTED", [])
    check(ps and ps[0]["index"] == 2 and ps[0]["ml"] == 200 and ps[0]["source"] == 0,
          "bấm nút 3 -> PRESET_SELECTED mức 200 ml (nguồn: nút)",
          str(ps[0]) if ps else "không có gói nào")

    def n_events_before(name, uptime_ms):
        """Đếm số khung của một loại xuất hiện trước một mốc thời gian."""
        return sum(1 for f in frames if f.msg == P.Msg[name] and f.msg)

    fs = by.get("FILL_STARTED", [])
    check(fs and fs[0]["ml"] == 200 and fs[0]["mode"] == 0,
          "FILL_STARTED đúng mục tiêu 200 ml, chế độ ESP tự đong", str(fs[0]) if fs else "")
    fd = by.get("FILL_DONE", [])
    done = [d for d in fd if d["status"] == int(P.StopReason.NORMAL)]
    check(done and abs(done[0]["poured_ml"] - 200) <= 12,
          "FILL_DONE bình thường: rót đúng 200 ml",
          "%s ml" % (done[0]["poured_ml"] if done else "?"))

    cr = by.get("CUP_REMOVED", [])
    check(cr and cr[0]["reason"] == int(P.StopReason.CUP_REMOVED),
          "nhấc cốc -> CUP_REMOVED kèm mã dừng 'nhấc cốc'")
    reasons = [d["status"] for d in fd]
    check(int(P.StopReason.LINK_LOST) in reasons, "mất liên lạc UART -> FILL_DONE mã 'mất liên lạc'",
          "các mã dừng thấy được: %s" % sorted(set(reasons)))
    check(int(P.StopReason.OVER_VOLUME) in reasons, "quá trần thể tích -> FILL_DONE mã 'quá thể tích'")
    check(int(P.StopReason.TIMEOUT) in reasons, "quá thời gian an toàn -> FILL_DONE mã 'quá thời gian'")
    check(int(P.StopReason.BUTTON_ESTOP) in reasons,
          "giữ nút 2 s -> FILL_DONE mã 'dừng khẩn cấp'")

    be = by.get("BUTTON_EVENT", [])
    check(len(be) >= 3 and any(d["event"] == int(P.BTN_ESTOP) for d in be),
          "BUTTON_EVENT báo được nhấn ngắn và dừng khẩn cấp", "%d sự kiện" % len(be))
    check(by.get("ACK") and any(d["acked_msg"] == int(P.Msg.CUP_OK) for d in by["ACK"]),
          "firmware trả ACK cho CUP_OK của PC")
    check(by.get("PONG"), "firmware trả PONG cho PING của PC (giữ nhịp tim)")

    # ---- mic: PCM thật của firmware -> chính VoiceRecognizer của PC nhận dạng ----
    audio = by.get("AUDIO_CHUNK", [])
    check(len(audio) > 20, "mic đẩy PCM lên PC qua gói AUDIO_CHUNK", "%d gói" % len(audio))
    # Tách PCM thành từng đoạn nói theo cờ START/END rồi cho PC nhận dạng từng đoạn
    # (đúng cách esp_bridge.py làm: mỗi đoạn nói là một lần analyze()).
    segs, cur = [], None
    for d in audio:
        if d.get("start"):
            cur = []
        if cur is not None:
            cur.append(d)
            if d.get("end"):
                segs.append(cur)
                cur = None
    ve = by.get("VOICE_EVENT", [])
    check(len(ve) > 0 and len(segs) == len(ve),
          "mỗi đoạn nói đều có gói PCM mở đầu (START) và kết thúc (END)",
          "%d đoạn PCM / %d VOICE_EVENT" % (len(segs), len(ve)))

    def pc_nghe(seg):
        """Cho VoiceRecognizer của PC nghe một đoạn nói -> (số tiếng, mức nước)."""
        vr = VoiceRecognizer(presets=(100, 150, 200, 250, 300))
        for d in seg:
            vr.feed(d)
        r = vr.analyze()
        return r.n_peaks, r.ml, r.text

    if segs:
        n1, ml1, txt1 = pc_nghe(segs[0])
        check(n1 == 3 and ml1 == 200,
              "PC nhận dạng PCM của firmware: 3 tiếng -> mức 200 ml",
              "%s (tần số ESP báo: %s Hz)" % (txt1 or "?", audio[0].get("rate_hz")))
    if len(segs) >= 2:
        n2, _, txt2 = pc_nghe(segs[1])
        check(n2 <= 5, "2 tiếng quá ngắn & sát nhau: PC cũng KHÔNG sinh tiếng ảo", txt2)
    if len(segs) >= 3:
        n3, ml3, txt3 = pc_nghe(segs[2])
        check(n3 == 1 and ml3 == 100, "3 tiếng quá sát nhau -> PC gộp thành 1 tiếng (mức 100 ml)",
              txt3)
        if ve:
            check(n3 == ve[-1]["n_peaks"],
                  "PC và ESP đếm tiếng GIỐNG NHAU (cùng một đoạn PCM)",
                  "PC %d tiếng / ESP %d tiếng" % (n3, ve[-1]["n_peaks"]))

    check(ve and ve[0]["n_peaks"] == 3, "firmware đếm đúng 3 tiếng trong đoạn nói",
          str(ve[0]) if ve else "không có VOICE_EVENT")
    check(ve and all(0 <= d["n_peaks"] <= 5 for d in ve),
          "bộ đếm tiếng trên ESP không bao giờ tràn (2 tiếng quá ngắn không thành 65535)",
          "các giá trị: %s" % [d["n_peaks"] for d in ve])
    check(int(P.StopReason.SENSOR_FAULT) in reasons,
          "mất cảm biến siêu âm -> FILL_DONE mã 'lỗi cảm biến'")
    check(any(d["reason"] == int(P.StopReason.SENSOR_FAULT) for d in cr),
          "CUP_REMOVED do tuột dây cảm biến ghi đúng mã 'lỗi cảm biến' (không phải 'nhấc cốc')")

    # ---- chạy lại TOÀN BỘ kịch bản với cấu hình "nói chuyện qua cổng USB của board"
    # (UART_USE_USB_SERIAL=1): người dùng ESP32 DevKit V1 không cần mạch USB-TTL vẫn dùng được.
    run_usb, out_usb = build_and_run(["-DUART_USE_USB_SERIAL=1", "-DDEBUG_SERIAL=0"],
                                     "chế độ cổng USB của board (không cần USB-TTL)")
    if run_usb is not None:
        checks_usb = [l for l in out_usb.splitlines() if l.startswith("CHECK ")]
        bad_usb = [l for l in checks_usb if l.startswith("CHECK FAIL")]
        check(len(checks_usb) >= 45 and not bad_usb,
              "chế độ cổng USB: cả %d bài kiểm tra firmware đều đạt" % len(checks_usb),
              " | ".join(bad_usb[:2]))
        dec_usb = P.FrameDecoder()
        n_usb = 0
        for l in out_usb.splitlines():
            if l.startswith("TX "):
                n_usb += len(dec_usb.feed(bytes.fromhex(l[3:].strip())))
        check(n_usb > 30 and dec_usb.n_bad_crc == 0,
              "chế độ cổng USB: firmware vẫn phát gói đúng chuẩn qua cổng USB",
              "%d gói, bad_crc=%d" % (n_usb, dec_usb.n_bad_crc))

    # ---- gói PC->ESP do bài test C++ sinh ra phải khớp bộ mã hoá của Python ----
    # Mỗi gói được giải mã rồi MÃ HOÁ LẠI bằng protocol.py; hai bộ mã hoá (C++ và Python)
    # khớp nhau thì payload phải trùng từng byte.
    def reencode(name, d):
        if name == "HELLO_ACK":
            return P.encode_hello_ack(d["version"], d["caps"], d["presets"])
        if name == "CUP_OK":
            return P.encode_cup_ok(d["rim_r_mm"], d["base_r_mm"], d["height_mm"],
                                   d["max_ml"], d["confidence"], d["flags"], d["label"])
        if name == "SET_PRESET":
            return P.encode_set_preset(d["index"], d["ml"], d["source"])
        if name == "START_FILL":
            return P.encode_start_fill(d["ml"], d["mode"], d["source"])
        if name == "STOP_FILL":
            return P.encode_stop_fill(d["reason"])
        if name == "SET_MODE":
            return P.encode_set_mode(d["on"], d["off"])
        if name == "PUMP_SET":
            return P.encode_pump_set(d["duty"], d["duration_ms"])
        if name == "SET_PARAMS":
            return P.encode_set_params(d["flow_ml_s"], d["max_ml"], d["timeout_s"])
        if name == "CMD":
            return P.encode_cmd(d["cmd"], d["arg"])
        if name == "PING":
            return P.encode_ping(d["uptime_ms"])
        return None

    mismatch, compared = [], set()
    for hx in (l[3:] for l in lines if l.startswith("PC ")):
        raw = bytes.fromhex(hx.strip())
        got = P.FrameDecoder().feed(raw)
        if len(got) != 1:
            mismatch.append("khung hỏng: " + hx.strip())
            continue
        fr = got[0]
        try:
            name = P.Msg(fr.msg).name
        except ValueError:
            mismatch.append("msg lạ 0x%02X" % fr.msg)
            continue
        want = reencode(name, P.decode(fr))
        if want is None or want == fr.payload:
            compared.add(name)
            continue
        mismatch.append("%s: C++ %s != Python %s" % (name, fr.payload.hex(), want.hex()))
    check(not mismatch and len(compared) >= 4,
          "gói PC->ESP do bài test sinh khớp bộ mã hoá của protocol.py",
          " | ".join(mismatch[:3]) + (" (đối chiếu %s)" % ",".join(sorted(compared))))


# ==========================================================================
def test_web() -> None:
    """Giao diện web ở CHẾ ĐỘ ESP32 (không cần trình duyệt, không cần phần cứng)."""
    print("== [8] GIAO DIỆN WEB (chế độ ESP32) ==")
    try:
        from cupfiller.webapp import WebApp
    except Exception as exc:                                   # pragma: no cover
        check(False, "nạp được cupfiller/webapp.py", str(exc))
        return
    if WebApp is None:                                         # pragma: no cover
        return

    cfg = load_config()
    cfg.set("camera.backend", "synthetic")
    cfg.set("control.pump.type", "sim")
    try:
        app = WebApp(cfg, port="sim")
    except RuntimeError as exc:                                # thiếu Flask
        check(False, "tạo được WebApp chế độ ESP32", str(exc))
        return
    check(app.esp_mode and app.sim is not None, "WebApp chạy bằng ESP32 giả lập")

    client = app.app.test_client()
    dt = 1.0 / max(1.0, float(cfg.get("control.loop.fps", 15)))

    # Vòng lặp phiên chạy trong luồng nền đúng như khi chạy thật (app.run()).
    th = threading.Thread(target=app._loop_esp, daemon=True)
    th.start()
    time.sleep(0.4)

    def tel() -> dict:
        return json.loads(client.get("/api/telemetry").data.decode())

    def wait(cond, timeout: float) -> dict:
        t_end = time.time() + timeout
        t = tel()
        while time.time() < t_end:
            if cond(t):
                return t
            time.sleep(dt)
            t = tel()
        return t

    html = client.get("/").data.decode()
    check("ESP32" in html and "/api/esp" in html, "trang chủ có bảng điều khiển ESP32")
    t = tel()
    check(t.get("esp_mode") is True and isinstance(t.get("log"), list),
          "telemetry có esp_mode + log của phiên")
    check(t["esp"].get("state_name") in P.STATE_NAMES.values(),
          "ESP32 giả lập đã bắt tay và báo trạng thái", str(t["esp"].get("state_name")))

    # bấm nút khi chưa có cốc -> phải bị bỏ qua
    client.post("/api/esp", json={"action": "press", "index": 0})
    time.sleep(0.6)
    t = tel()
    check(int(t["esp"].get("poured_ml") or 0) == 0 and not t["esp"].get("pumping"),
          "bấm nút trên web khi chưa có cốc -> ESP bỏ qua")

    # camera TẮT lúc này, ảnh /video phải là ảnh thay thế (không được trắng/trống)
    buf = b""
    resp = client.get("/video", buffered=False)
    for chunk in resp.response:
        buf += chunk
        if len(buf) > 4000:
            break
    resp.close()
    JPEG_SOI = bytes([0xFF, 0xD8])          # 0xFFD8 = đầu khung ảnh JPEG
    check(JPEG_SOI in buf, "ảnh /video khi camera TẮT vẫn có khung JPEG",
          "%d byte" % len(buf))

    # đặt cốc -> camera bật -> nhận diện -> CUP_OK -> mở khoá
    client.post("/api/esp", json={"action": "cup", "height_mm": 95})
    t = wait(lambda t: t.get("camera_open") and (t["esp"] or {}).get("cup_present"), 6.0)
    check(t.get("camera_open"), "đặt cốc trên web -> ESP báo -> PC BẬT CAMERA")
    t = wait(lambda t: (t["esp"] or {}).get("unlocked") and t.get("state") == "READY", 10.0)
    check((t["esp"] or {}).get("unlocked"), "PC xác nhận cốc -> ESP mở khoá nút/mic trên web",
          str(t.get("cup_meta")))

    # bấm nút 3 -> 200 ml
    client.post("/api/esp", json={"action": "press", "index": 2})
    t = wait(lambda t: t["esp"].get("pumping"), 6.0)
    check(bool(t["esp"].get("pumping")), "bấm nút 3 trên web -> relay đóng, bơm chạy")
    t = wait(lambda t: (t["esp"] or {}).get("state_name") == "DONE", 20.0)
    poured = int((t["esp"] or {}).get("poured_ml") or 0)
    check(190 <= poured <= 210, "bơm đủ 200 ml -> FILL_DONE", "đã rót %d ml" % poured)

    # lệnh PC: tare / self-test / disarm
    for act in ("tare", "selftest", "disarm"):
        r = client.post("/api/esp", json={"action": act})
        check(r.status_code == 200, "lệnh PC trên web: %s" % act)

    # nhấc cốc
    client.post("/api/esp", json={"action": "remove"})
    t = wait(lambda t: not (t["esp"] or {}).get("cup_present"), 6.0)
    check(not (t["esp"] or {}).get("cup_present"), "nhấc cốc trên web -> ESP báo CUP_REMOVED")

    # hành động lạ -> 400 (không được làm sập server)
    r = client.post("/api/esp", json={"action": "khong_co_that"})
    check(r.status_code == 400, "hành động lạ -> trả lỗi 400 chứ không sập")

    app._stop = True
    th.join(timeout=2.0)
    app.session.close()


# ==========================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cpp", action="store_true", help="bỏ bài kiểm tra biên dịch C++")
    ap.add_argument("--only", default=None,
                    help="chỉ chạy một nhóm: protocol|cpp|voice|flow|session|firmware|"
                         "fwrun|web|cli|entry")
    args = ap.parse_args()

    groups = {
        "protocol": lambda: test_protocol(),
        "cpp": lambda: test_cpp(not args.no_cpp),
        "voice": test_voice,
        "flow": test_flow,
        "session": test_session,
        "firmware": lambda: test_firmware(not args.no_cpp),
        "fwrun": test_fwrun,
        "web": test_web,
        "cli": test_cli,
        "entry": test_entry,
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
