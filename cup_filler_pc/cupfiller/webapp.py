"""Giao diện web điều khiển máy rót (Flask + MJPEG) - HAI chế độ:

1. Chế độ thường (PC tự bơm):
       python3 tools/web.py --synthetic       (thử không cần phần cứng)
2. Chế độ ESP32 (bo ESP32 giữ cảm biến/nút/mic/relay, PC lo nhận diện cốc):
       python3 tools/web.py --port sim        (ESP32 giả lập - xem được ngay không cần gì)
       python3 tools/web.py --port COM5       (ESP32 thật)
   Ở chế độ này trang web có thêm bảng ESP32: trạng thái, cốc, khoá nút, ml đã rót và các
   nút bấm tương đương phần cứng (đặt cốc / nhấc cốc / bấm nút 1-5 / nói 1-5 tiếng) - chỉ
   hiện khi chạy ESP32 giả lập.

Truy cập từ điện thoại/máy tính trong mạng LAN của Pi:  http://<ip-pi>:8080
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional

import cv2
import numpy as np

from . import protocol as P
from .config import load_config
from .controller import FillController, State
from .detection import draw_overlay
from .camera import open_camera
from .pump import open_pump
from .synthetic import SyntheticCupCamera, CupScene

try:
    from flask import Flask, Response, jsonify, request
except ImportError:  # pragma: no cover
    Flask = None


HTML = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Máy rót nước tự động</title>
<style>
 body{font-family:system-ui,sans-serif;background:#101418;color:#e8ecef;margin:0}
 header{padding:10px 16px;background:#1b2229;border-bottom:1px solid #2c3640}
 main{display:flex;gap:16px;flex-wrap:wrap;padding:16px}
 .panel{background:#171d23;border:1px solid #2c3640;border-radius:10px;padding:12px}
 video,img.cam{width:min(640px,92vw);border-radius:8px;background:#000}
 button{background:#2a9d8f;border:0;color:#fff;padding:10px 16px;border-radius:8px;
        font-size:15px;margin:3px;cursor:pointer}
 button.preset{background:#33404b}
 button.preset.on{background:#e9c46a;color:#222}
 button.stop{background:#e76f51}
 .kpi{display:flex;gap:18px;flex-wrap:wrap;margin-top:8px}
 .kpi div{background:#10161b;border-radius:8px;padding:8px 12px;min-width:96px}
 .kpi b{display:block;font-size:20px}
 .state{font-weight:700;padding:4px 10px;border-radius:6px;background:#33404b}
 .state.DONE{background:#2a9d8f}.state.FAULT{background:#e76f51}
 .state.COARSE,.state.FINE,.state.PRIME,.state.TOPUP{background:#e9c46a;color:#222}
 #alert{color:#f4a261;min-height:20px}
</style></head><body>
<header><b>💧 Máy rót nước tự động</b> — nhận diện cốc trong suốt bằng thị giác máy tính</header>
<main>
 <div class="panel">
   <img class="cam" id="cam" src="/video" alt="camera">
   <div id="alert"></div>
 </div>
 <div class="panel" style="min-width:260px">
   <div>Mức rót: <span id="presets"></span></div>
   <div style="margin-top:10px">
     <button id="start">▶ Rót</button>
     <button id="stop" class="stop">■ Dừng</button>
   </div>
   <div class="kpi">
     <div><b id="h">0.0</b>mm nước</div>
     <div><b id="tgt">0.0</b>mm đích</div>
     <div><b id="vol">0</b>ml ước lượng</div>
     <div><b id="pum">0.0</b>PWM</div>
   </div>
   <p>Trạng thái: <span class="state" id="state">IDLE</span></p>
   <p id="report" style="color:#9fd3c8"></p>
 </div>
</main>
<script>
let cur = 0;
function el(id){return document.getElementById(id)}
async function post(j){await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(j)})}
async function loop(){
  const r = await fetch('/api/telemetry'); const t = await r.json();
  el('h').textContent = t.h_mm; el('tgt').textContent = t.target_mm;
  el('vol').textContent = t.volume_ml; el('pum').textContent = t.pwm;
  const st = el('state'); st.textContent = t.state; st.className = 'state ' + t.state;
  el('alert').textContent = t.alert || '';
  if (t.report && t.report.preset_ml){
    el('report').textContent = '✅ Xong: đã bơm ' + t.report.pumped_ml + ' ml, vạch nước '
      + t.report.height_mm + ' mm (lệch ' + t.report.err_mm + ' mm) sau ' + t.report.time_s + 's';
  }
  if (!cur){ cur = t.preset_ml || t.presets[0]; }
  const box = el('presets');
  if (!box.childElementCount){
    t.presets.forEach(p=>{
      const b = document.createElement('button'); b.className='preset'; b.textContent=p+' ml';
      b.onclick = async ()=>{ cur=p; await post({action:'preset', preset_ml:p}); mark(); };
      box.appendChild(b);
    });
    mark();
  }
  function mark(){ [...box.children].forEach(b=>b.classList.toggle('on', parseFloat(b.textContent)===cur)); }
  setTimeout(loop, 250);
}
el('start').onclick = ()=>post({action:'start'});
el('stop').onclick  = ()=>post({action:'stop'});
loop();
</script></body></html>
"""




HTML_ESP = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Máy rót nước - chế độ ESP32</title>
<style>
 body{font-family:system-ui,sans-serif;background:#101418;color:#e8ecef;margin:0}
 header{padding:10px 16px;background:#1b2229;border-bottom:1px solid #2c3640}
 main{display:flex;gap:16px;flex-wrap:wrap;padding:16px}
 .panel{background:#171d23;border:1px solid #2c3640;border-radius:10px;padding:12px}
 img.cam{width:min(640px,92vw);border-radius:8px;background:#000}
 button{background:#2a9d8f;border:0;color:#fff;padding:9px 14px;border-radius:8px;
        font-size:14px;margin:3px;cursor:pointer}
 button.preset{background:#33404b}
 button.stop{background:#e76f51}
 button.off{background:#3a3f45;color:#888;cursor:not-allowed}
 .kpi{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px}
 .kpi div{background:#10161b;border-radius:8px;padding:8px 12px;min-width:92px}
 .kpi b{display:block;font-size:19px}
 .state{font-weight:700;padding:4px 10px;border-radius:6px;background:#33404b}
 .state.READY,.state.DONE{background:#2a9d8f}.state.FAULT{background:#e76f51}
 .state.POURING,.state.POURING_PC{background:#e9c46a;color:#222}
 .lock{color:#e76f51}.unlock{color:#2a9d8f;font-weight:700}
 #log{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px;color:#9fd3c8;
      max-height:190px;overflow:auto}
 #err{color:#f4a261;min-height:18px}
 .row{margin-top:8px}
 .sim{background:#1f2a33;border:1px dashed #3d4b57}
</style></head><body>
<header><b>💧 Máy rót nước</b> — chế độ <b>ESP32</b>: bo ESP32 giữ cảm biến/nút/mic/relay,
máy tính chỉ nhận diện cốc và ra lệnh qua UART</header>
<main>
 <div class="panel">
   <img class="cam" id="cam" src="/video" alt="camera">
   <div id="err"></div>
   <div class="row" id="log"></div>
 </div>
 <div class="panel" style="min-width:320px">
   <div>ESP32: <span class="state" id="est">?</span>
        <span id="lock" class="lock">🔒 nút đang khoá</span></div>
   <div class="kpi">
     <div><b id="poured">0</b>ml đã rót</div>
     <div><b id="target">0</b>ml mục tiêu</div>
     <div><b id="cup">—</b>cốc</div>
     <div><b id="pump">—</b>bơm</div>
   </div>
   <p>Máy tính: <span id="pstate">—</span> — <span id="pmsg"></span></p>
   <p>Cốc: <span id="cmeta">chưa nhận diện</span></p>
   <div class="row sim" id="simbox">
     <div><b>Thao tác trên bo ESP32 giả lập</b></div>
     <div>
       <button onclick="act('cup')">📥 Đặt cốc</button>
       <button class="stop" onclick="act('remove')">📤 Nhấc cốc</button>
     </div>
     <div>
       <span>Bấm nút:</span>
       <button class="preset" onclick="act('press',{index:0})">1</button>
       <button class="preset" onclick="act('press',{index:1})">2</button>
       <button class="preset" onclick="act('press',{index:2})">3</button>
       <button class="preset" onclick="act('press',{index:3})">4</button>
       <button class="preset" onclick="act('press',{index:4})">5</button>
     </div>
     <div>
       <span>Nói (số tiếng):</span>
       <button class="preset" onclick="act('voice',{index:0})">1</button>
       <button class="preset" onclick="act('voice',{index:1})">2</button>
       <button class="preset" onclick="act('voice',{index:2})">3</button>
     </div>
   </div>
   <div class="row">
     <b>Máy tính ra lệnh</b>
     <div>
       <button onclick="act('ok')">✅ Xác nhận cốc (CUP_OK)</button>
       <button onclick="act('start')">▶ Rót mức đang chọn</button>
       <button class="stop" onclick="act('stop')">■ Dừng bơm</button>
     </div>
     <div>
       <button class="preset" onclick="act('tare')">Tare cảm biến</button>
       <button class="preset" onclick="act('disarm')">Disarm (khoá lại)</button>
       <button class="preset" onclick="act('selftest')">Self-test relay</button>
     </div>
   </div>
 </div>
</main>
<script>
function el(id){return document.getElementById(id)}
async function act(action, extra){
  const body = Object.assign({action:action}, extra||{});
  const r = await fetch('/api/esp',{method:'POST',headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify(body)});
  const j = await r.json(); render(j);
}
function render(t){
  const e = t.esp || {};
  const st = el('est'); st.textContent = e.state_name || '?'; st.className = 'state ' + (e.state_name||'');
  const lk = el('lock');
  if (e.unlocked){ lk.textContent = '🔓 đã mở khoá nút/mic'; lk.className = 'unlock'; }
  else { lk.textContent = '🔒 nút đang khoá (chờ PC xác nhận cốc)'; lk.className = 'lock'; }
  el('poured').textContent = e.poured_ml ?? 0;
  el('target').textContent = e.target_ml ?? 0;
  el('cup').textContent = e.cup_present ? 'có' : 'không';
  el('pump').textContent = e.pumping ? 'ON' : 'tắt';
  el('pstate').textContent = t.state; el('pmsg').textContent = t.message || '';
  el('err').textContent = t.error || '';
  const cm = t.cup_meta;
  el('cmeta').textContent = cm ? (cm.label + ' — cao ' + cm.height_mm + ' mm, Ø' +
      Math.round(cm.r_rim_mm*2) + ' mm, rót tối đa ' + Math.round(cm.can_pour_ml) + ' ml'
      + (t.waterline_found ? ', nước ' + t.h_mm + ' mm' : '')) : 'chưa nhận diện';
  el('simbox').style.display = t.sim ? '' : 'none';
  el('log').textContent = (t.log || []).join('\\n');
}
async function loop(){
  try { const r = await fetch('/api/telemetry'); render(await r.json()); } catch(e){}
  setTimeout(loop, 400);
}
loop();
</script></body></html>
"""


class WebApp:
    """Giao diện web. ``port`` != None -> CHẾ ĐỘ ESP32 (dùng ``AutoFillSession``)."""

    def __init__(self, cfg, port: Optional[str] = None):
        if Flask is None:
            raise RuntimeError("Thiếu Flask: chạy  pip install flask  rồi thử lại.")
        self.cfg = cfg
        self.port = port
        self.esp_mode = bool(port)
        self.session = None
        self.cam = None
        self.pump = None
        self.ctl = None
        scene = None
        if cfg.get("camera.backend", "") == "synthetic":
            scene = SyntheticCupCamera(CupScene(), fps=float(cfg.get("control.loop.fps", 15)))
        self._scene = scene
        self._frame = None
        self._lock = threading.Lock()
        self._stop = False
        self._log: List[str] = []
        if self.esp_mode:
            from .session import AutoFillSession

            if port == "sim":
                cfg.set("control.pump.type", "sim")
            self.session = AutoFillSession(cfg, port=port, scene=scene,
                                           log=self._log_line, on_state=self._on_state)
            self.sim = getattr(self.session.bridge, "simulator", None)
        else:
            self.sim = None
            self.cam = open_camera(cfg, scene=scene)
            self.pump = open_pump(cfg, getattr(self.cam, "synthetic", None))
            self.ctl = FillController(cfg, self.cam, self.pump)
        self._fallback_jpg = self._placeholder({})       # /video luôn có ảnh để trả về
        self.app = Flask(__name__)
        self._routes()

    # ------------------------------------------------------------------
    def _log_line(self, msg: str) -> None:
        msg = str(msg)
        print(msg, flush=True)
        with self._lock:
            self._log.append(msg)
            if len(self._log) > 200:
                del self._log[:100]

    def _on_state(self, name: str, fields: dict) -> None:
        pass

    @property
    def log_tail(self) -> List[str]:
        with self._lock:
            return list(self._log[-14:])

    # ------------------------------------------------------------------
    def _placeholder(self, tel) -> bytes:
        """Ảnh thay thế khi camera đang TẮT (chưa có cốc) - để web không bị trống."""
        img = np.full((360, 640, 3), 22, np.uint8)
        cv2.putText(img, "CAMERA DANG TAT (tiet kiem dien)", (28, 150),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 200, 190), 2)
        cv2.putText(img, "Dat coc len cam bien ESP32 de bat camera", (28, 195),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
        esp = (tel or {}).get("esp") or {}
        cv2.putText(img, "ESP32: %s | coc: %s | da rot: %s/%s ml"
                    % (esp.get("state_name"), "co" if esp.get("cup_present") else "khong",
                       esp.get("poured_ml"), esp.get("target_ml")),
                    (28, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (140, 170, 210), 1)
        _, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpg.tobytes()

    def _overlay_esp(self, tel) -> bytes:
        esp = (tel or {}).get("esp") or {}
        frame = self.session.frame
        if frame is None:
            return self._placeholder(tel)
        det = self.session.det
        vis = draw_overlay(
            frame, det, target_mm=None,
            text=[
                "ESP: %s | coc=%s | xac nhan=%s | bom=%s"
                % (esp.get("state_name"), "co" if esp.get("cup_present") else "khong",
                   "roi" if esp.get("pc_confirmed") else "chua",
                   "ON" if esp.get("pumping") else "tat"),
                "PC : %s - %s" % (tel.get("state"), tel.get("message")),
                "rot: %s/%s ml" % (esp.get("poured_ml"), esp.get("target_ml")),
                (tel.get("error") or ""),
            ],
        )
        _, jpg = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpg.tobytes()

    def _loop(self):
        if self.esp_mode:
            return self._loop_esp()
        dt = 1.0 / self.ctl.fps
        while not self._stop:
            t0 = time.time()
            tel = self.ctl.tick(dt)
            det = self.ctl.last_det
            if self.ctl.frame is not None and det is not None:
                vis = draw_overlay(
                    self.ctl.frame,
                    det,
                    target_mm=tel["target_mm"] or None,
                    text=[
                        "%s  preset %g ml" % (tel["state"], tel["preset_ml"]),
                        "nuoc %.1f/%.1f mm  ~%.0f ml  pwm %.2f"
                        % (tel["h_mm"], tel["target_mm"], tel["volume_ml"], tel["pwm"]),
                    ],
                )
                _, jpg = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 80])
                with self._lock:
                    self._frame = jpg.tobytes()
            time.sleep(max(0.0, dt - (time.time() - t0)))

    def _loop_esp(self):
        dt = 1.0 / max(1.0, float(self.cfg.get("control.loop.fps", 15)))
        tel = {}
        while not self._stop:
            t0 = time.time()
            try:
                self.session.tick(dt)
                tel = self.session.telemetry()
            except Exception as exc:                     # pragma: no cover
                self._log_line("[web] lỗi vòng lặp ESP: %s" % exc)
                time.sleep(0.2)
                continue
            jpg = self._overlay_esp(tel)
            with self._lock:
                self._frame = jpg
            time.sleep(max(0.0, dt - (time.time() - t0)))

    def _routes(self):
        app = self.app

        @app.route("/")
        def index():
            return HTML_ESP if self.esp_mode else HTML

        @app.route("/video")
        def video():
            def gen():
                while True:
                    with self._lock:
                        buf = self._frame or self._fallback_jpg
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf + b"\r\n")
                    time.sleep(0.06)

            return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @app.route("/api/telemetry")
        def telemetry():
            if self.esp_mode:
                tel = self.session.telemetry()
                tel["esp_mode"] = True
                tel["sim"] = self.sim is not None
                tel["log"] = self.log_tail
                tel["port"] = self.port
                return jsonify(tel)
            return jsonify(self.ctl.telemetry())

        @app.route("/api/esp", methods=["POST"])
        def esp_action():
            """Điều khiển bo ESP32 từ web (giống hệt thao tác tay trên phần cứng)."""
            if not self.esp_mode:
                return jsonify({"error": "đang chạy chế độ thường (không có ESP32)"}), 400
            j = request.get_json(force=True, silent=True) or {}
            act = str(j.get("action") or "")
            idx = int(j.get("index") or 0)
            br = self.session.bridge
            try:
                if act == "cup" and self.sim is not None:
                    self.sim.place_cup(float(j.get("height_mm", 95.0)))
                elif act == "remove" and self.sim is not None:
                    self.sim.remove_cup()
                elif act == "press" and self.sim is not None:
                    self.sim.press_button(idx, int(j.get("hold_ms", 120)))
                elif act == "voice" and self.sim is not None:
                    self.sim.speak(idx, int(j.get("confidence", 85)))
                elif act == "ok":
                    br.confirm_cup(rim_r_mm=float(j.get("rim_r_mm", 33.0)),
                                   base_r_mm=float(j.get("base_r_mm", 30.0)),
                                   height_mm=float(j.get("height_mm", 95.0)),
                                   label=str(j.get("label", "web")))
                elif act == "reject":
                    br.reject_cup(P.RejectReason.NOT_FOUND, str(j.get("text", "web_reject")))
                elif act == "start":
                    br.start_fill(float(j["ml"]) if j.get("ml") else None)
                elif act == "stop":
                    br.stop_fill(P.StopReason.PC_REQUEST)
                elif act == "preset":
                    br.set_preset(float(j.get("ml", 200)))
                elif act == "tare":
                    br.cmd(P.Cmd.TARE)
                elif act == "disarm":
                    br.cmd(P.Cmd.DISARM)
                elif act == "selftest":
                    br.cmd(P.Cmd.SELF_TEST)
                elif act == "mode_pc":
                    br.set_pc_controls_pump(bool(j.get("on", True)))
                else:
                    return jsonify({"error": "hành động không hỗ trợ: %r" % act}), 400
            except Exception as exc:
                return jsonify({"error": "lỗi khi gửi lệnh: %s" % exc}), 500
            tel = self.session.telemetry()
            tel["esp_mode"] = True
            tel["sim"] = self.sim is not None
            tel["log"] = self.log_tail
            return jsonify(tel)

        @app.route("/api/control", methods=["POST"])
        def control():
            j = request.get_json(force=True, silent=True) or {}
            act = j.get("action")
            if act == "start":
                self.ctl.start()
            elif act == "stop":
                self.ctl.stop()
            elif act == "preset":
                self.ctl.set_preset(float(j.get("preset_ml", self.ctl.preset_ml)))
            return jsonify(self.ctl.telemetry())

    # ------------------------------------------------------------------
    def run(self):
        th = threading.Thread(target=self._loop, daemon=True)
        th.start()
        host = self.cfg.get("web.host", "0.0.0.0")
        port = int(self.cfg.get("web.port", 8080))
        print("Mở trình duyệt: http://<ip-may>:%d  (Ctrl+C để dừng)" % port)
        if self.esp_mode:
            print("Chế độ ESP32 (cổng %s) — %s"
                  % (self.port, "giả lập" if self.sim is not None else "phần cứng thật"))
        try:
            self.app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
        finally:
            self._stop = True
            if self.esp_mode:
                self.session.close()
            else:
                self.pump.off()
                self.cam.release()
