"""Giao diện web điều khiển máy rót (Flask + MJPEG).

Chạy:  python3 tools/web.py --config config/settings.yaml
       python3 tools/web.py --synthetic      (thử không cần phần cứng)
Truy cập từ điện thoại/máy tính trong mạng LAN của Pi:  http://<ip-pi>:8080
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

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


class WebApp:
    def __init__(self, cfg):
        if Flask is None:
            raise RuntimeError("Thiếu Flask: chạy  pip install flask  rồi thử lại.")
        self.cfg = cfg
        scene = None
        if cfg.get("camera.backend", "") == "synthetic":
            scene = SyntheticCupCamera(CupScene(), fps=float(cfg.get("control.loop.fps", 15)))
        self.cam = open_camera(cfg, scene=scene)
        self.pump = open_pump(cfg, getattr(self.cam, "synthetic", None))
        self.ctl = FillController(cfg, self.cam, self.pump)
        self._frame = None
        self._lock = threading.Lock()
        self._stop = False
        self.app = Flask(__name__)
        self._routes()

    # ------------------------------------------------------------------
    def _loop(self):
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

    def _routes(self):
        app = self.app

        @app.route("/")
        def index():
            return HTML

        @app.route("/video")
        def video():
            def gen():
                while True:
                    with self._lock:
                        buf = self._frame
                    if buf:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf + b"\r\n")
                    time.sleep(0.06)

            return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @app.route("/api/telemetry")
        def telemetry():
            return jsonify(self.ctl.telemetry())

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
        try:
            self.app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
        finally:
            self._stop = True
            self.pump.off()
            self.cam.release()
