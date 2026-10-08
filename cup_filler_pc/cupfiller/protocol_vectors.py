"""Bộ VECTOR VÀNG của giao thức - nguồn sự thật duy nhất cho tính tương thích
giữa Python (PC) và C++ (firmware ESP32).

Mỗi vector gồm:
  * ``name``    : tên kịch bản (dòng trong file vector)
  * ``msg``     : mã gói tin (0xNN)
  * ``seq``     : số thứ tự khung
  * ``frame``   : BYTE THẬT trên dây (AA 55 ... CRC) - do phía Python tạo
  * ``payload`` : phần payload (không kể header/CRC)
  * ``fields``  : các trường đã giải mã, dùng để đối chiếu hai bên
  * ``dir``     : "esp2pc" (firmware gửi, PC nhận) hay "pc2esp" (PC gửi, firmware nhận)

Cách dùng:
  * ``tools/gen_protocol_vectors.py`` xuất file ``firmware/host_test/protocol_vectors.txt``
  * ``tools/test_comms.py`` kiểm tra: (1) Python giải mã ra đúng ``fields``,
    (2) firmware C++ (``firmware/host_test/test_protocol.cpp``) cũng cho kết quả như vậy.

ĐỔI GIAO THỨC THÌ PHẢI:
  1. sửa ``cupfiller/protocol.py``  +  ``firmware/esp32_cup_filler/protocol.h``
  2. chạy ``python tools/gen_protocol_vectors.py``  (ghi lại file vector)
  3. chạy ``python tools/test_comms.py``            (Python + C++ phải cùng PASS)
"""
from __future__ import annotations

import struct
from typing import Dict, List

from . import protocol as P

# Quy ước trường trong file vector (chữ thường, không dấu cách):
#   ESP -> PC : version caps flow_ml_s_x10 presets distance_mm baseline_mm height_mm
#               flags uptime_ms reason index ml source mode max_ml poured_ml pct
#               target_ml state status elapsed_ms kind confidence n_peaks rms dur_ms
#               seq8 n_samples first last event press_ms code text acked_msg
#   PC  -> ESP: rim_r_mm base_r_mm label duty_pct duration_ms timeout_ms
#               timeout_s pulse_ms cmd arg on off
#
# Lưu ý: giá trị CHUỖI (text/log/label) không được chứa dấu cách -> dùng "_".


def _sc(name: str, msg, seq: int, frame_kwargs: Dict, fields: Dict, direction: str) -> dict:
    frame = P.encode(msg, seq=seq, **frame_kwargs)
    return {
        "name": name,
        "msg": int(msg),
        "seq": int(seq),
        "frame": frame,
        "payload": frame[5:-2],
        "fields": dict(fields),
        "dir": direction,
    }


def build_vectors() -> List[dict]:
    """Danh sách vector vàng (đúng thứ tự hai bên phải khớp)."""
    v: List[dict] = []

    # ---------------- ESP32 -> PC ----------------
    v.append(_sc("hello_boot", P.Msg.HELLO, 1, {
        "payload": P.encode_hello(
            1, P.ESP_CAP_BUTTONS | P.ESP_CAP_VOICE | P.ESP_CAP_US_SENSOR,
            [100, 150, 200, 250, 300], 40.0)}, {
        "version": 1,
        "caps": 7,
        "flow_ml_s_x10": 400,
        "presets": [100, 150, 200, 250, 300]}, "esp2pc"))

    v.append(_sc("hello_flow_cal", P.Msg.HELLO, 7, {
        "payload": P.encode_hello(1, P.ESP_CAP_BUTTONS, [100, 200], 57.5)}, {
        "version": 1, "caps": 1, "flow_ml_s_x10": 575, "presets": [100, 200]}, "esp2pc"))

    v.append(_sc("cup_placed", P.Msg.CUP_PLACED, 2, {
        "payload": P.encode_cup_placed(92, 150, 58, 0x01, 12345)}, {
        "distance_mm": 92, "baseline_mm": 150, "height_mm": 58,
        "flags": 1, "uptime_ms": 12345}, "esp2pc"))

    v.append(_sc("cup_placed_retry", P.Msg.CUP_PLACED, 9, {
        "payload": P.encode_cup_placed(0, 150, 0, 0x02, 5)}, {
        "distance_mm": 0, "baseline_mm": 150, "height_mm": 0,
        "flags": 2, "uptime_ms": 5}, "esp2pc"))

    v.append(_sc("cup_removed", P.Msg.CUP_REMOVED, 3, {
        "payload": P.encode_cup_removed(P.StopReason.CUP_REMOVED, 20000)}, {
        "reason": 2, "uptime_ms": 20000}, "esp2pc"))

    v.append(_sc("preset_selected_button", P.Msg.PRESET_SELECTED, 4, {
        "payload": P.encode_preset_selected(2, 200, P.Source.BUTTON)}, {
        "index": 2, "ml": 200, "source": 0}, "esp2pc"))

    v.append(_sc("preset_selected_voice", P.Msg.PRESET_SELECTED, 5, {
        "payload": P.encode_preset_selected(4, 300, P.Source.VOICE)}, {
        "index": 4, "ml": 300, "source": 1}, "esp2pc"))

    v.append(_sc("fill_started_esp", P.Msg.FILL_STARTED, 6, {
        "payload": P.encode_fill_started(200, P.FillMode.ESP_OPEN_LOOP, P.Source.BUTTON, 318)}, {
        "ml": 200, "mode": 0, "source": 0, "max_ml": 318}, "esp2pc"))

    v.append(_sc("fill_started_pc", P.Msg.FILL_STARTED, 8, {
        "payload": P.encode_fill_started(150, P.FillMode.PC_CLOSED_LOOP, P.Source.PC, 500)}, {
        "ml": 150, "mode": 1, "source": 2, "max_ml": 500}, "esp2pc"))

    v.append(_sc("fill_progress", P.Msg.FILL_PROGRESS, 10, {
        "payload": P.encode_fill_progress(120, 60, 200, P.EspState.POURING)}, {
        "poured_ml": 120, "pct": 60, "target_ml": 200, "state": 4}, "esp2pc"))

    v.append(_sc("fill_progress_start", P.Msg.FILL_PROGRESS, 11, {
        "payload": P.encode_fill_progress(0, 0, 100, P.EspState.POURING)}, {
        "poured_ml": 0, "pct": 0, "target_ml": 100, "state": 4}, "esp2pc"))

    v.append(_sc("fill_done_normal", P.Msg.FILL_DONE, 12, {
        "payload": P.encode_fill_done(200, 200, P.StopReason.NORMAL, 4900)}, {
        "poured_ml": 200, "target_ml": 200, "status": 0, "elapsed_ms": 4900}, "esp2pc"))

    v.append(_sc("fill_done_cup_removed", P.Msg.FILL_DONE, 13, {
        "payload": P.encode_fill_done(61, 200, P.StopReason.CUP_REMOVED, 1500)}, {
        "poured_ml": 61, "target_ml": 200, "status": 2, "elapsed_ms": 1500}, "esp2pc"))

    v.append(_sc("status_ready", P.Msg.STATUS, 14, {
        "payload": P.encode_status(P.EspState.READY, P.FL_CUP_PRESENT | P.FL_PC_CONFIRMED |
                                   P.FL_VOICE_ACTIVE | P.FL_LINK_OK, 0, 200, 318, 33000)}, {
        "state": 3, "flags": 0x1B, "poured_ml": 0, "target_ml": 200,
        "max_ml": 318, "uptime_ms": 33000}, "esp2pc"))

    v.append(_sc("status_pouring", P.Msg.STATUS, 15, {
        "payload": P.encode_status(P.EspState.POURING, P.FL_CUP_PRESENT | P.FL_PC_CONFIRMED |
                                   P.FL_PUMPING | P.FL_LINK_OK, 120, 200, 318, 34500)}, {
        "state": 4, "flags": 0x17, "poured_ml": 120, "target_ml": 200,
        "max_ml": 318, "uptime_ms": 34500}, "esp2pc"))

    v.append(_sc("status_manual", P.Msg.STATUS, 16, {
        "payload": P.encode_status(P.EspState.MANUAL, P.FL_CUP_PRESENT | P.FL_MANUAL, 0, 200, 500, 99000)}, {
        "state": 7, "flags": 0x21, "poured_ml": 0, "target_ml": 200,
        "max_ml": 500, "uptime_ms": 99000}, "esp2pc"))

    v.append(_sc("voice_event_2peaks", P.Msg.VOICE_EVENT, 17, {
        "payload": P.encode_voice_event(P.VOICE_CLAPS, 1, 87, 2, 1420, 780)}, {
        "kind": 2, "index": 1, "confidence": 87, "n_peaks": 2, "rms": 1420,
        "dur_ms": 780}, "esp2pc"))

    v.append(_sc("voice_event_zero", P.Msg.VOICE_EVENT, 18, {
        "payload": P.encode_voice_event(P.VOICE_UNKNOWN, 0, 0, 0, 0, 0)}, {
        "kind": 0, "index": 0, "confidence": 0, "n_peaks": 0, "rms": 0, "dur_ms": 0}, "esp2pc"))

    v.append(_sc("audio_chunk", P.Msg.AUDIO_CHUNK, 19, {
        "payload": P.encode_audio_chunk(0, P.AU_START, [100, -100, 32767, -32768])}, {
        "seq8": 0, "flags": 1, "n_samples": 4, "first": 100, "last": -32768}, "esp2pc"))

    v.append(_sc("audio_chunk_end_silence", P.Msg.AUDIO_CHUNK, 20, {
        "payload": P.encode_audio_chunk(9, P.AU_END, [0, 0, 0, 0])}, {
        "seq8": 9, "flags": 2, "n_samples": 4, "first": 0, "last": 0}, "esp2pc"))

    v.append(_sc("audio_chunk_96", P.Msg.AUDIO_CHUNK, 21, {
        "payload": P.encode_audio_chunk(255, P.AU_END, list(range(-48, 48)))}, {
        "seq8": 255, "flags": 2, "n_samples": 96, "first": -48, "last": 47}, "esp2pc"))

    v.append(_sc("button_short", P.Msg.BUTTON_EVENT, 22, {
        "payload": P.encode_button_event(2, P.BTN_SHORT, 40, P.EspState.READY)}, {
        "index": 2, "event": 0, "press_ms": 40, "state": 3}, "esp2pc"))

    v.append(_sc("button_long", P.Msg.BUTTON_EVENT, 23, {
        "payload": P.encode_button_event(4, P.BTN_LONG, 1600, P.EspState.READY)}, {
        "index": 4, "event": 1, "press_ms": 1600, "state": 3}, "esp2pc"))

    v.append(_sc("button_estop", P.Msg.BUTTON_EVENT, 24, {
        "payload": P.encode_button_event(0, P.BTN_ESTOP, 2100, P.EspState.POURING)}, {
        "index": 0, "event": 2, "press_ms": 2100, "state": 4}, "esp2pc"))

    v.append(_sc("error_esp", P.Msg.ERROR, 25, {
        "payload": P.encode_error(1, "PC_khong_tra_loi_CUP_OK")}, {
        "code": 1, "text": "PC_khong_tra_loi_CUP_OK"}, "esp2pc"))

    v.append(_sc("ack_esp", P.Msg.ACK, 26, {"payload": P.encode_ack(P.Msg.CUP_OK, 0)}, {
        "acked_msg": 0x82, "status": 0}, "esp2pc"))

    v.append(_sc("pong", P.Msg.PONG, 27, {"payload": P.encode_pong(654321, P.EspState.POURING, 0x17)}, {
        "uptime_ms": 654321, "state": 4, "flags": 0x17}, "esp2pc"))

    v.append(_sc("log", P.Msg.LOG, 28, {"payload": b"DA_DAT_COC_58mm"}, {
        "text": "DA_DAT_COC_58mm"}, "esp2pc"))

    # ---------------- PC -> ESP32 ----------------
    v.append(_sc("hello_ack", P.Msg.HELLO_ACK, 30, {
        "payload": P.encode_hello_ack(1, P.CAP_VISION | P.CAP_VOICE_ASR | P.CAP_CUP_INFO,
                                      [100, 150, 200, 250, 300])}, {
        "version": 1, "caps": 11, "presets": [100, 150, 200, 250, 300]}, "pc2esp"))

    v.append(_sc("cup_ok", P.Msg.CUP_OK, 31, {
        "payload": P.encode_cup_ok(31.5, 26.0, 102.4, 350, 0.92, 0x03, "cupA")}, {
        "rim_r_mm": "31.5", "base_r_mm": "26.0", "height_mm": "102.4", "max_ml": 350,
        "confidence": "0.92", "flags": 3, "label": "cupA"}, "pc2esp"))

    v.append(_sc("cup_ok_zero", P.Msg.CUP_OK, 32, {
        "payload": P.encode_cup_ok(0, 0, 0, 0, 0, 0, "")}, {
        "rim_r_mm": "0.0", "base_r_mm": "0.0", "height_mm": "0.0", "max_ml": 0,
        "confidence": "0.0", "flags": 0, "label": ""}, "pc2esp"))

    v.append(_sc("cup_reject", P.Msg.CUP_REJECT, 33, {
        "payload": P.encode_cup_reject(P.RejectReason.NOT_FOUND, "khong_thay_coc")}, {
        "reason": 0, "text": "khong_thay_coc"}, "pc2esp"))

    v.append(_sc("set_preset", P.Msg.SET_PRESET, 34, {
        "payload": P.encode_set_preset(1, 150, P.Source.PC)}, {
        "index": 1, "ml": 150, "source": 2}, "pc2esp"))

    v.append(_sc("start_fill_esp_open_loop", P.Msg.START_FILL, 35, {
        "payload": P.encode_start_fill(200, P.FillMode.ESP_OPEN_LOOP, P.Source.PC, 20000)}, {
        "ml": 200, "mode": 0, "source": 2, "timeout_ms": 20000}, "pc2esp"))

    v.append(_sc("start_fill_pc_closed_loop", P.Msg.START_FILL, 36, {
        "payload": P.encode_start_fill(250, P.FillMode.PC_CLOSED_LOOP, P.Source.PC, 0)}, {
        "ml": 250, "mode": 1, "source": 2, "timeout_ms": 0}, "pc2esp"))

    v.append(_sc("stop_fill", P.Msg.STOP_FILL, 37, {
        "payload": P.encode_stop_fill(P.StopReason.PC_REQUEST)}, {
        "reason": 1}, "pc2esp"))

    v.append(_sc("set_mode_on", P.Msg.SET_MODE, 38, {
        "payload": P.encode_set_mode(P.MODE_PC_CONTROLS_PUMP | P.MODE_VOICE_STREAM,
                                     P.MODE_LINK_STOP_PUMP)}, {
        "on": 0x0003, "off": 0x0004}, "pc2esp"))

    v.append(_sc("pump_set_50", P.Msg.PUMP_SET, 39, {
        "payload": P.encode_pump_set(0.5, 0)}, {
        "duty_pct": 50, "duration_ms": 0}, "pc2esp"))

    v.append(_sc("pump_set_pulse", P.Msg.PUMP_SET, 40, {
        "payload": P.encode_pump_set(0.75, 800)}, {
        "duty_pct": 75, "duration_ms": 800}, "pc2esp"))

    v.append(_sc("set_params_all", P.Msg.SET_PARAMS, 41, {
        "payload": P.encode_set_params(42.5, 400, 30, 500)}, {
        "flow_ml_s_x10": 425, "max_ml": 400, "timeout_s": 30, "pulse_ms": 500}, "pc2esp"))

    v.append(_sc("set_params_keep", P.Msg.SET_PARAMS, 42, {
        "payload": P.encode_set_params(None, None, None, None)}, {
        "flow_ml_s_x10": 0xFFFF, "max_ml": 0xFFFF, "timeout_s": 0xFFFF,
        "pulse_ms": 0xFFFF}, "pc2esp"))

    v.append(_sc("cmd_tare", P.Msg.CMD, 43, {
        "payload": P.encode_cmd(P.Cmd.TARE, 0)}, {
        "cmd": 2, "arg": 0}, "pc2esp"))

    v.append(_sc("cmd_self_test", P.Msg.CMD, 44, {
        "payload": P.encode_cmd(P.Cmd.SELF_TEST, 300)}, {
        "cmd": 4, "arg": 300}, "pc2esp"))

    v.append(_sc("ping", P.Msg.PING, 45, {"payload": P.encode_ping(123456)}, {
        "uptime_ms": 123456}, "pc2esp"))

    return v


# ---------------------------------------------------------------------------
def byte_hex(b: bytes) -> str:
    return " ".join("%02X" % x for x in b)


def render() -> str:
    """Sinh nội dung file vector (text, dễ đọc bằng cả Python lẫn C++)."""
    lines = [
        "# VECTOR VANG GIAO THUC CUPFILLER - KHONG SUA TAY!",
        "#",
        "# File nay do tools/gen_protocol_vectors.py sinh ra tu cupfiller/protocol_vectors.py.",
        "# No la 'ban ghi tren day' de PC (Python) va ESP32 (C++) doi chieu tung byte.",
        "#",
        "# Dinh dang moi dong:",
        "#   <ten> <msg_hex> <seq> <frame_hex> | <payload_hex> | key=value ...",
        "#   - msg_hex  : 1 byte ma goi tin (vd 02)",
        "#   - frame_hex: TOAN BO khung tren day, gom AA 55 ... CRC (cach nhau dau cach)",
        "#   - payload_hex: chi phan payload (de doi chieu nhanh)",
        "#   - key=value: cac truong da giai ma; chuoi dung '_' thay cho dau cach",
        "#   - dir=esp2pc|pc2esp cho biet ben nao gui",
        "#",
        "# Quy tac: ESP->PC thi firmware phai TAO RA dung frame_hex nay;",
        "#          PC->ESP thi Python phai TAO RA dung frame_hex nay.",
        "",
    ]
    for item in build_vectors():
        keys = " ".join(
            "%s=%s" % (k, v if isinstance(v, str) else
                       (",".join(str(x) for x in v) if isinstance(v, (list, tuple)) else v))
            for k, v in item["fields"].items()
        )
        lines.append("%s msg=%02X seq=%d dir=%s frame=%s | payload=%s | %s" % (
            item["name"], item["msg"], item["seq"], item["dir"],
            byte_hex(item["frame"]), byte_hex(item["payload"]), keys))
    lines.append("")
    return "\n".join(lines)


def parse_line(line: str) -> dict:
    """Đọc 1 dòng vector thành dict (dùng cho test Python)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return {}
    head, _, tail = line.partition("|")
    hparts = head.split()
    item = {"name": hparts[0], "fields": {}}
    for token in hparts[1:]:
        k, _, v = token.partition("=")
        item[k] = v
    tail_parts = tail.split("|")
    if tail_parts:
        for token in tail_parts[0].split():
            k, _, v = token.partition("=")
            if k == "payload":
                item["payload"] = bytes(int(x, 16) for x in v.split())
    for token in (tail_parts[1] if len(tail_parts) > 1 else "").split():
        k, _, v = token.partition("=")
        item["fields"][k] = v
    item["msg"] = int(item.get("msg", "0"), 16)
    item["seq"] = int(item.get("seq", "0"))
    item["frame"] = bytes(int(x, 16) for x in item.get("frame", "").split())
    return item


# ---------------------------------------------------------------------------
#  Tự kiểm tra: Python phải khớp với chính bộ vector
# ---------------------------------------------------------------------------
def _as_vector_fields(parsed: dict) -> dict:
    """Chuyển kết quả parse_xxx() của Python về tên trường trong file vector."""
    out: Dict[str, object] = {}
    for k, val in parsed.items():
        if k in ("state_name", "cup_present", "pc_confirmed", "pumping", "voice_active", "link_ok",
                 "start", "end"):
            continue
        if k == "seq":                      # AUDIO_CHUNK: tránh lẫn với seq của khung
            out["seq8"] = val
            continue
        if k == "samples":
            out["first"] = val[0] if val else 0
            out["last"] = val[-1] if val else 0
            continue
        if k == "duty":
            out["duty_pct"] = int(round(float(val) * 100))
        elif k == "flow_ml_s":
            out["flow_ml_s_x10"] = 0xFFFF if val is None else int(round(float(val) * 10))
        elif isinstance(val, (list, tuple)):
            out[k] = list(val)
        else:
            out[k] = val
    return out


def compare(parsed: dict, fields: Dict[str, object], name: str = "") -> None:
    """So kết quả ``parse_xxx()`` với các trường mong đợi trong bảng vector.

    Ném ``AssertionError`` nếu lệch (dùng cho tools/test_comms.py).
    """
    got = _as_vector_fields(parsed)
    for key, want in fields.items():
        if key not in got:
            raise AssertionError("%s: parser khong tra ve truong %r" % (name, key))
        val = got[key]
        if val is None and isinstance(want, int) and want == 0xFFFF:
            continue                      # None = "giữ nguyên" -> trên dây là 0xFFFF
        if isinstance(want, list):
            ok = list(val) == list(want)
        elif isinstance(want, str):
            ok = (abs(float(val) - float(want)) < 1e-6) if "." in want else (str(val) == want)
        else:
            ok = int(val) == int(want)
        if not ok:
            raise AssertionError("%s: truong %s = %r, mong doi %r" % (name, key, val, want))


def self_check() -> List[str]:
    """Kiểm tra Python <-> bảng vector. Trả về danh sách lỗi (rỗng = đạt)."""
    errors: List[str] = []
    for item in build_vectors():
        frame = item["frame"]
        dec = P.FrameDecoder()
        frames = dec.feed(frame)
        if len(frames) != 1:
            errors.append("%s: FrameDecoder tra ve %d khung" % (item["name"], len(frames)))
            continue
        f = frames[0]
        if int(f.msg) != item["msg"] or f.seq != item["seq"] or f.payload != item["payload"]:
            errors.append("%s: header/payload khong khop" % item["name"])
            continue
        parser = P.PARSERS.get(P.Msg(int(f.msg)))
        if parser is None:
            errors.append("%s: thieu parser cho 0x%02X" % (item["name"], int(f.msg)))
            continue
        try:
            compare(parser(f.payload), item["fields"], item["name"])
        except AssertionError as exc:
            errors.append(str(exc))
    return errors


__all__ = ["build_vectors", "render", "parse_line", "self_check", "compare", "byte_hex"]
