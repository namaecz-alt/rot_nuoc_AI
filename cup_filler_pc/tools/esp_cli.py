#!/usr/bin/env python3
"""BẢNG ĐIỀU KHIỂN ESP32 (thật hoặc giả lập) bằng dòng lệnh.

Dùng để kiểm tra phần cứng TRƯỚC KHI lắp camera, hoặc để thử toàn bộ luồng mà
không cần phần cứng.

Chạy:

    python3 tools/esp_cli.py --port sim            # ESP32 GIẢ LẬP (không cần gì)
    python3 tools/esp_cli.py --port COM5           # ESP32 thật (Windows)
    python3 tools/esp_cli.py --port /dev/ttyUSB0   # ESP32 thật (Linux/macOS)
    python3 tools/esp_cli.py --port tcp://127.0.0.1:5555   # ESP32 giả lập ở máy khác
    python3 tools/esp_cli.py --port sim --demo     # chạy kịch bản tự động rồi thoát
    python3 tools/esp_cli.py --port sim -c "cup" -c "ok 300" -c "press 3" -c watch=6

Gõ ``help`` trong chương trình để xem danh sách lệnh.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller import protocol as P                        # noqa: E402
from cupfiller.config import load_config                   # noqa: E402
from cupfiller.esp_bridge import EspBridge                 # noqa: E402

STOP_NAMES = {0: "đủ ml", 1: "PC yêu cầu dừng", 2: "nhấc cốc", 3: "quá thời gian",
              4: "mất liên lạc", 5: "DỪNG KHẨN CẤP", 6: "quá thể tích (an toàn)",
              7: "lỗi cảm biến"}

HELP = """
LỆNH (gõ rồi Enter):
  status                 xem trạng thái ESP (LED, cốc, bơm, ml, liên lạc)
  watch [giây]           in mọi gói tin ESP gửi lên (mặc định 5 giây, 0 = mãi)
  events [n]             in n sự kiện gần nhất
  ------------------------------------------------ (thao tác như người dùng thật)
  cup [cao_mm]           [chỉ --sim] đặt cốc lên khay
  remove                 [chỉ --sim] nhấc cốc ra
  press <1..5> [ms]      [chỉ --sim] bấm nút (ms >= 2000 = dừng khẩn cấp)
  voice <1..5>           [chỉ --sim] nói mức nước thứ N (1 = 100 ml)
  ------------------------------------------------ (thao tác của MÁY TÍNH)
  ok [ml] [conf]         gửi CUP_OK -> MỞ KHOÁ nút/mic trên ESP
  reject [lý_do]         gửi CUP_REJECT (không nhận diện được cốc)
  preset <1..5>          PC chọn hộ mức nước
  start [ml] [esp|pc]    ra lệnh bơm (esp = ESP tự đong, pc = PC vòng kín)
  stop                   dừng bơm ngay
  duty <0..100>          [chế độ pc] đặt độ mở bơm (%)
  mode pc|esp            bật/tắt chế độ PC điều khiển bơm
  params <ml/s> [max] [s] hiệu chuẩn lưu lượng / trần ml / thời gian tối đa
  tare                   đo lại mặt khay (nhấc cốc ra trước!)
  selftest               bật relay 300 ms (kiểm tra bơm)
  disarm / reset         khoá lại nút/mic (chưa xác nhận cốc) / đưa về IDLE
  ping                   kiểm tra liên lạc
  sim                    [chỉ --sim] tóm tắt trạng thái ESP giả lập
  quit / q               thoát
"""


class Cli:
    def __init__(self, args) -> None:
        self.args = args
        cfg = load_config(args.config)
        self.bridge = EspBridge(cfg, port=args.port, baud=args.baud,
                                log=self._log, on_event=self._on_event)
        self.sim = self.bridge.simulator
        self.watch_until = 0.0
        self.quiet = bool(args.quiet)
        self.show_types = set()
        self.t_last_status = 0.0

    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def _on_event(self, name: str, ev: dict) -> None:
        """In ra những sự kiện quan trọng (như màn hình của máy thật)."""
        fields = ev.get("fields") or {}
        if self.watch_until > time.time():
            print("   %-14s %s" % (name, _short(fields)), flush=True)
            return
        if name == "hello":
            self._log("[ESP] đã khởi động: preset %s, lưu lượng %.1f ml/s"
                      % (fields.get("presets"), float(fields.get("flow_ml_s") or 0)))
        elif name == "cup_placed":
            self._log("[ESP] ĐẶT CỐC (cao %.0f mm) -> PC phải bật camera + nhận diện"
                      % float(fields.get("height_mm") or 0))
        elif name == "cup_removed":
            self._log("[ESP] NHẤC CỐC -> đã ngắt bơm (nếu đang bơm)")
        elif name == "preset_selected":
            self._log("[ESP] chọn mức %s ml (nguồn: %s)"
                      % (fields.get("ml"), {0: "nút", 1: "giọng nói", 2: "PC"}.get(
                          int(fields.get("source") or 0), "?")))
        elif name == "fill_started":
            self._log("[ESP] BẮT ĐẦU BƠM: mục tiêu %s ml (chế độ %s)"
                      % (fields.get("ml"), "ESP tự đong" if int(fields.get("mode") or 0) == 0
                         else "PC điều khiển"))
        elif name == "fill_progress":
            self._log("[ESP] đang rót: %s ml / %s ml (%s%%)"
                      % (fields.get("poured_ml"), fields.get("target_ml"), fields.get("pct")))
        elif name == "fill_done":
            self._log("[ESP] KẾT THÚC: đã rót %s ml / %s ml, mã dừng %s (%s)"
                      % (fields.get("poured_ml"), fields.get("target_ml"), fields.get("status"),
                         STOP_NAMES.get(int(fields.get("status") or 0), "?")))
        elif name == "voice":
            v = ev.get("voice") or {}
            if v.get("ok"):
                self._log("[PC ] nghe ra %.0f ml (nguồn %s)" % (float(v.get("ml") or 0),
                                                                v.get("source")))
            else:
                self._log("[PC ] chưa nghe ra mức nước (peaks=%s)" % v.get("n_peaks"))
        elif name == "locked_attempt":
            self._log("[ESP] THAO TÁC BỊ KHOÁ: chưa có CUP_OK từ PC")
        elif name == "error":
            self._log("[ESP] LỖI: %s" % fields.get("text"))
        elif name == "link_lost":
            self._log("[ESP] MẤT LIÊN LẠC VỚI PC")
        elif name == "cup_ok_sent":
            self._log("[PC ] đã gửi CUP_OK -> ESP mở khoá nút/mic")

    # ------------------------------------------------------------------
    def pump_status_line(self) -> str:
        st = self.bridge.status
        bits = ["trạng thái=%s" % st.state_name,
                "cốc=%s" % ("có" if st.cup_present else "không"),
                "PC xác nhận=%s" % ("rồi" if st.pc_confirmed else "chưa"),
                "bơm=%s" % ("BẬT" if st.pumping else "tắt"),
                "%.0f/%.0f ml" % (st.poured_ml, st.target_ml or 0),
                "trần=%.0f ml" % (st.max_ml or 0),
                "liên lạc=%s" % ("OK" if st.link_ok else "MẤT")]
        if self.sim is not None:
            bits.append("giả lập: %s" % self.sim.state_name())
        return " | ".join(bits)

    def print_status(self) -> None:
        print("  " + self.pump_status_line())
        v = self.bridge.last_voice
        if v is not None:
            print("  giọng nói: ok=%s ml=%.0f nguồn=%s" % (v.ok, v.ml, v.source))

    # ------------------------------------------------------------------
    def handle(self, line: str) -> bool:
        """Xử lý một dòng lệnh. Trả False nếu cần thoát."""
        parts = line.strip().split()
        if not parts:
            return True
        cmd, rest = parts[0].lower(), parts[1:]
        sim = self.sim
        link = self.bridge.link

        if cmd in ("quit", "q", "exit"):
            return False
        if cmd in ("help", "?"):
            print(HELP)
        elif cmd in ("status", "s"):
            self.print_status()
        elif cmd == "sim":
            if sim is None:
                print("  (không phải chế độ giả lập)")
            else:
                print("  %s | sự kiện: %s" % (sim.state_name(), ", ".join(sim.event_names()[-8:])))
        elif cmd in ("watch", "listen", "w"):
            secs = float(rest[0]) if rest else 5.0
            self.watch_until = time.time() + secs if secs > 0 else 1e18
            print("  ... theo dõi mọi gói tin ESP gửi lên%s"
                  % (" (Ctrl+C để dừng)" if secs <= 0 else " trong %.0f giây" % secs))
            t0 = time.time()
            try:
                while time.time() < self.watch_until:
                    self.bridge.tick(0.02)
                    time.sleep(0.02)
            except KeyboardInterrupt:
                pass
            self.watch_until = 0.0
        elif cmd == "events":
            n = int(rest[0]) if rest else 12
            for ev in list(self.bridge.events)[-n:]:
                print("   %-16s %s" % (ev["name"], _short(ev.get("fields") or {})))
        # --- thao tác vật lý (chỉ giả lập) ---
        elif cmd == "cup":
            if sim is None:
                print("  (hãy ĐẶT CỐC THẬT lên khay; lệnh này chỉ dùng cho --sim)")
            else:
                sim.place_cup(float(rest[0]) if rest else None)
                print("  đã đặt cốc giả lập (cao %.0f mm) - chờ 0,3 s cho cảm biến" %
                      sim.cup_height_mm)
        elif cmd == "remove":
            if sim is None:
                print("  (hãy NHẤC CỐC THẬT ra; lệnh này chỉ dùng cho --sim)")
            else:
                sim.remove_cup()
                print("  đã nhấc cốc giả lập")
        elif cmd == "press":
            if sim is None:
                print("  (hãy BẤM NÚT THẬT; lệnh này chỉ dùng cho --sim)")
            else:
                i = int(rest[0]) - 1 if rest else 2
                hold = int(rest[1]) if len(rest) > 1 else 120
                sim.press_button(i, hold)
                print("  đã bấm nút %d (giữ %d ms)" % (i + 1, hold))
        elif cmd == "voice":
            if sim is None:
                print("  (hãy NÓI vào mic; lệnh này chỉ dùng cho --sim)")
            else:
                i = int(rest[0]) - 1 if rest else 1
                sim.speak(i)
                print("  đã giả lập nói mức %d" % (i + 1))
        # --- thao tác của PC ---
        elif cmd == "ok":
            ml = float(rest[0]) if rest else 300.0
            conf = float(rest[1]) if len(rest) > 1 else 0.95
            self.bridge.confirm_cup(rim_r_mm=33.0, base_r_mm=30.0, height_mm=95.0,
                                    max_ml=ml, confidence=conf, label="cli_cup")
        elif cmd == "reject":
            reason = int(rest[0]) if rest else int(P.RejectReason.NOT_FOUND)
            self.bridge.reject_cup(reason, "cli_reject")
        elif cmd == "preset":
            if not rest:
                print("  dùng: preset <1..5>")
            else:
                i = int(rest[0]) - 1
                self.bridge.set_preset(self.bridge.presets[i], i)
                print("  PC đã đặt mức %d ml" % self.bridge.presets[i])
        elif cmd == "start":
            ml = float(rest[0]) if rest else None
            mode = None
            if len(rest) > 1:
                mode = int(P.FillMode.PC_CLOSED_LOOP if rest[1].lower() == "pc"
                           else P.FillMode.ESP_OPEN_LOOP)
            self.bridge.start_fill(ml, mode)
        elif cmd == "stop":
            self.bridge.stop_fill()
            print("  đã gửi STOP_FILL")
        elif cmd == "duty":
            pct = float(rest[0]) if rest else 0.0
            self.bridge.pump_set(max(0.0, min(1.0, pct / 100.0)))
            print("  PUMP_SET duty=%.0f%%" % pct)
        elif cmd == "mode":
            want = (rest[0].lower() if rest else "pc")
            on = P.MODE_PC_CONTROLS_PUMP if want.startswith("pc") else 0
            off = 0 if want.startswith("pc") else P.MODE_PC_CONTROLS_PUMP
            self.bridge.pc_controls_pump = want.startswith("pc")
            link.send_set_mode(on, off)
            print("  chế độ PC điều khiển bơm: %s" % ("BẬT" if want.startswith("pc") else "TẮT"))
        elif cmd == "params":
            flow = float(rest[0]) if rest else None
            mx = int(rest[1]) if len(rest) > 1 else None
            to = int(rest[2]) if len(rest) > 2 else None
            self.bridge.send_params(flow, mx, to)
            print("  đã gửi SET_PARAMS (flow=%s ml/s, max=%s ml, timeout=%s s)" % (flow, mx, to))
        elif cmd == "tare":
            self.bridge.cmd(P.Cmd.TARE)
            print("  đã gửi CMD:TARE (nhớ nhấc cốc ra trước)")
        elif cmd == "selftest":
            self.bridge.cmd(P.Cmd.SELF_TEST)
            print("  đã gửi CMD:SELF_TEST (relay bật 300 ms)")
        elif cmd == "disarm":
            self.bridge.cmd(P.Cmd.DISARM)
            print("  đã khoá lại nút/mic (cần CUP_OK mới dùng được)")
        elif cmd == "reset":
            self.bridge.cmd(P.Cmd.RESET_STATE)
            print("  đã đưa ESP về IDLE")
        elif cmd == "ping":
            link.send_ping()
            print("  đã gửi PING")
        else:
            print("  không hiểu lệnh %r - gõ 'help'" % cmd)
        return True

    # ------------------------------------------------------------------
    def run(self) -> int:
        print("Kết nối: %s" % self.bridge.port)
        # chờ HELLO đầu tiên (ESP thật gửi ngay khi khởi động; có thể đã gửi trước đó)
        t0 = time.time()
        while time.time() - t0 < 2.0 and self.bridge.status.fw_version == 0:
            self.bridge.tick(0.02)
            time.sleep(0.02)
        if self.bridge.status.fw_version:
            print("Đã bắt tay với ESP32: firmware v%d, preset %s, lưu lượng %.1f ml/s"
                  % (self.bridge.status.fw_version, self.bridge.status.presets,
                     self.bridge.status.flow_ml_s))
        else:
            print("CHƯA thấy HELLO từ ESP32 - kiểm tra dây RX/TX (phải nối CHÉO), GND chung, "
                  "và đúng cổng COM. Vẫn tiếp tục ở chế độ gõ lệnh.")

        if self.args.demo:
            return self.run_demo()

        for c in self.args.cmd:
            print("> " + c)
            if not self.handle(c):
                break
            for _ in range(30):          # cho ESP kịp phản hồi
                self.bridge.tick(0.02)
                time.sleep(0.01)

        if self.args.cmd:
            self.bridge.close()
            return 0

        print("Gõ 'help' để xem lệnh, 'quit' để thoát.")
        try:
            while True:
                self.bridge.tick(0.02)
                line = input("esp> ")
                if not self.handle(line):
                    break
        except (EOFError, KeyboardInterrupt):
            print()
        finally:
            self.bridge.close()
        return 0

    # ------------------------------------------------------------------
    def run_demo(self) -> int:
        """Kịch bản tự động đúng như thao tác thật (dùng được cả với --sim)."""
        sim = self.sim
        print("\n=== KỊCH BẢN TỰ ĐỘNG ===")

        def pump(seconds: float) -> None:
            t0 = time.time()
            while time.time() - t0 < seconds:
                self.bridge.tick(0.02)
                time.sleep(0.02)

        def wait(text: str, cond, timeout: float) -> bool:
            print("  → chờ: %s" % text)
            t0 = time.time()
            while time.time() - t0 < timeout:
                self.bridge.tick(0.02)
                if cond():
                    pump(0.1)
                    print("     ✔ %s" % text)
                    return True
                time.sleep(0.02)
            print("     ✘ QUÁ THỜI GIAN: %s" % text)
            return False

        pump(0.3)
        self.print_status()

        if sim is not None:
            print("  → (giả lập) đặt cốc cao 95 mm lên khay")
            sim.place_cup(95.0)
        else:
            print("  → HÃY ĐẶT CỐC LÊN KHAY NGAY BÂY GIỜ...")
        if not wait("ESP báo CUP_PLACED (PC mới bật camera)", 
                    lambda: self.bridge.status.cup_present, 30.0 if sim is None else 3.0):
            self.bridge.close()
            return 1

        print("  → PC gửi CUP_OK (thay cho bước nhận diện cốc bằng camera)")
        self.bridge.confirm_cup(rim_r_mm=33.0, base_r_mm=30.0, height_mm=95.0,
                                max_ml=300.0, confidence=0.95, label="demo")
        wait("ESP MỞ KHOÁ nút/mic", lambda: self.bridge.status.unlocked, 3.0)
        self.print_status()

        if sim is not None:
            print("  → (giả lập) bấm nút thứ 3 = 200 ml")
            sim.press_button(2)
        else:
            print("  → HÃY BẤM NÚT 200 ml (nút thứ 3) NGAY BÂY GIỜ...")
        wait("ESP bắt đầu bơm", lambda: self.bridge.status.pumping, 20.0)
        wait("rót xong", lambda: not self.bridge.status.pumping and
             self.bridge.status.poured_ml > 0, 40.0)

        if sim is not None:
            print("  → (giả lập) nhấc cốc ra")
            sim.remove_cup()
            wait("ESP báo CUP_REMOVED", lambda: not self.bridge.status.cup_present, 3.0)
        self.print_status()
        print("=== HẾT KỊCH BẢN — nếu mọi bước đều ✔ thì ESP32 + dây UART chạy tốt ===")
        self.bridge.close()
        return 0


def _short(fields: dict, n: int = 3) -> str:
    items = []
    for k, v in list(fields.items())[:n]:
        if isinstance(v, list):
            items.append("%s=%s" % (k, v[:4]))
        elif isinstance(v, (int, float)):
            items.append("%s=%.3g" % (k, v))
        else:
            items.append("%s=%s" % (k, str(v)[:14]))
    extra = "" if len(fields) <= n else " …"
    return " ".join(items) + extra


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bảng điều khiển ESP32 (thật hoặc giả lập)")
    ap.add_argument("--port", default="sim",
                    help="sim | COM5 | /dev/ttyUSB0 | tcp://host:port (mặc định: sim)")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--config", default=None, help="file settings.yaml (tuỳ chọn)")
    ap.add_argument("-c", "--cmd", action="append", default=[],
                    help="chạy một lệnh rồi thoát (lặp lại được nhiều lần)")
    ap.add_argument("--demo", action="store_true",
                    help="chạy kịch bản đặt cốc -> xác nhận -> bấm nút -> rót")
    ap.add_argument("--quiet", action="store_true", help="không in log của ESP")
    args = ap.parse_args(argv)
    try:
        return Cli(args).run()
    except KeyboardInterrupt:                                  # pragma: no cover
        print("\nĐã dừng.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
