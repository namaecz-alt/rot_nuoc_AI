"""Phiên làm việc tự động: CẢM BIẾN (ESP32) -> CAMERA + NHẬN DIỆN (PC) -> BƠM.

Đây là "nhạc trưởng" đúng theo luồng yêu cầu:

    1. Cốc được đặt lên cảm biến  -> ESP32 gửi CUP_PLACED
    2. **Chỉ khi đó PC mới mở camera** và chạy nhận diện cốc (tiết kiệm điện/USB,
       không bật camera thường trực)
    3. Nhận diện ổn định  -> gửi CUP_OK (hình học + thể tích tối đa) -> ESP mở khoá
       nút bấm và mic (trước đó bấm nút/nói đều bị bỏ qua)
    4. Người dùng nhấn nút chọn mức hoặc nói vào mic:
         * chế độ "esp": ESP tự đong theo lưu lượng đã hiệu chuẩn (PC chỉ giám sát)
         * chế độ "pc" : PC chạy vòng kín thị giác, điều khiển relay qua PUMP_SET
    5. Nhấc cốc -> ESP ngắt bơm ngay + gửi CUP_REMOVED -> PC ngừng và đóng camera
       sau một khoảng chờ.

Lớp này không tự mở cửa sổ: nó chỉ cung cấp ``tick(dt)`` + ``telemetry()`` để
``tools/run_pc.py`` hoặc giao diện web hiển thị.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import protocol as P
from .camera import open_camera
from .controller import FillController, State
from .detection import CupDetector, CupDetection
from .esp_bridge import EspBridge
from .level_yolo import WaterLevelDetector
from .synthetic import CupScene, SyntheticCupCamera

__all__ = ["SessionConfig", "AutoFillSession"]


@dataclass
class SessionConfig:
    """Tham số điều phối (lấy từ ``cfg.get("esp....")`` với giá trị mặc định hợp lý)."""

    auto_camera: bool = True            # bật camera theo sự kiện ESP
    camera_idle_close_s: float = 20.0   # không dùng bao lâu thì đóng camera
    recognize_delay_s: float = 0.25     # chờ cốc đứng yên sau khi đặt
    recognize_timeout_s: float = 6.0    # quá lâu chưa nhận ra -> báo CUP_REJECT
    recognize_stable: int = 4           # số khung ổn định cần có
    dry_run: bool = False               # không mở camera (kiểm thử giao thức thuần)
    loop_hz: float = 15.0


class AutoFillSession:
    def __init__(self, cfg, bridge: Optional[EspBridge] = None, port: Optional[str] = None,
                 detector=None, scene=None, log: Optional[Callable[[str], None]] = None,
                 on_state: Optional[Callable[[str, dict], None]] = None,
                 level_detector=None):
        self.cfg = cfg
        self.log = log or (lambda msg: print(msg, flush=True))
        self.bridge = bridge or EspBridge(cfg, port=port, log=self.log)
        self.bridge.on_cup_placed = self._on_cup_placed
        self.bridge.on_cup_removed = self._on_cup_removed
        self.bridge.on_event = self._on_esp_event
        self.on_state = on_state

        sc = SessionConfig(
            auto_camera=bool(cfg.get("esp.auto_camera", True)),
            camera_idle_close_s=float(cfg.get("esp.camera_idle_close_s", 20.0)),
            recognize_delay_s=float(cfg.get("esp.recognize_delay_s", 0.25)),
            recognize_timeout_s=float(cfg.get("esp.recognize_timeout_s", 6.0)),
            recognize_stable=int(cfg.get("esp.recognize_stable_frames", 4)),
            dry_run=bool(cfg.get("esp.dry_run", False)),
            loop_hz=float(cfg.get("control.loop.fps", 15.0)),
        )
        self.sc = sc
        self.detector = detector or CupDetector(cfg)
        # model mực nước của người dùng (weights/muc_nuoc_yolo.pt): tự tắt nếu thiếu
        self.level = level_detector if level_detector is not None else WaterLevelDetector(cfg)
        self.scene = scene

        self.cam = None
        self.ctl: Optional[FillController] = None
        self.last_pc_report: Optional[Dict] = None   # báo cáo lượt rót vòng kín gần nhất
        self.t_camera_open = 0.0
        self.t_last_use = 0.0
        self.t_last_use = time.time()
        self._pending: Optional[dict] = None
        self._t_pending = 0.0
        self._stable = 0
        self._prev_box = None
        self.det: Optional[CupDetection] = None
        self.state = "IDLE"                 # IDLE | RECOGNIZING | READY | POURING(pc) | ...
        self.message = "Chờ đặt cốc..."
        self.cup_meta: Dict = {}
        self.error = ""
        self.n_recognized = 0
        self.n_rejected = 0
        self.events: List[Dict] = []

    # ==================================================================
    #  Callback từ ESP
    # ==================================================================
    def _on_cup_placed(self, fields: dict) -> None:
        self._pending = fields
        self._t_pending = time.time()
        self._stable = 0
        self._prev_box = None
        self.state = "RECOGNIZING"
        self.message = "Đã thấy cốc - đang bật camera và nhận diện..."
        self._emit("recognize_start", fields)

    def _on_cup_removed(self, fields: dict) -> None:
        self._pending = None
        self.state = "IDLE"
        self.message = "Đã nhấc cốc"
        if self.ctl is not None and self.ctl.state in (State.COARSE, State.FINE, State.TOPUP,
                                                       State.PRIME):
            self.ctl.stop("CUP_REMOVED")
        self._emit("cup_removed", fields)

    def _on_esp_event(self, name: str, ev: dict) -> None:
        self._emit(name, ev.get("fields") or {})

    def _emit(self, name: str, fields: Optional[dict] = None) -> None:
        self.events.append({"t": time.time(), "name": name, "fields": fields or {}})
        if len(self.events) > 300:
            del self.events[:150]
        if self.on_state is not None:
            try:
                self.on_state(name, fields or {})
            except Exception as exc:                      # pragma: no cover
                self.log("[session] on_state lỗi: %s" % exc)

    # ==================================================================
    #  Camera: mở theo yêu cầu, đóng khi rảnh
    # ==================================================================
    @property
    def camera_open(self) -> bool:
        return self.cam is not None

    def open_camera(self) -> bool:
        if self.cam is not None:
            self.t_last_use = time.time()
            return True
        if self.sc.dry_run:
            return False
        try:
            scene = self.scene
            if scene is None and self.cfg.get("camera.backend", "") == "synthetic":
                scene = SyntheticCupCamera(CupScene(), fps=float(self.sc.loop_hz))
                self.scene = scene
            self.cam = open_camera(self.cfg, scene=scene)
            self.t_camera_open = time.time()
            self.log("[PC] đã BẬT CAMERA (%s) - chỉ bật khi cảm biến báo có cốc"
                     % getattr(self.cam, "backend_api", self.cam.backend))
            self._emit("camera_open")
            return True
        except Exception as exc:
            self.cam = None
            self.error = "CAMERA_ERROR: %s" % exc
            self.log("[PC] không mở được camera: %s" % exc)
            self.bridge.reject_cup(P.RejectReason.CAMERA_ERROR, "Khong mo duoc camera")
            self.state = "IDLE"
            return False

    def close_camera(self) -> None:
        if self.cam is None:
            return
        try:
            self.cam.release()
        except Exception:
            pass
        self.cam = None
        self.log("[PC] đã đóng camera (không dùng nữa - tiết kiệm điện/USB)")
        self._emit("camera_close")

    # ==================================================================
    #  Nhận diện
    # ==================================================================
    def _recognize(self, dt: float) -> None:
        """Chạy nhận diện cho tới khi ĐỦ CHẮN thì gửi CUP_OK, quá lâu thì CUP_REJECT."""
        if not self.camera_open and not self.open_camera():
            return
        self.t_last_use = time.time()
        if (time.time() - self._t_pending) < self.sc.recognize_delay_s:
            return
        ok, frame = self.cam.read()
        if not ok or frame is None:
            return
        det = self.detector.detect(frame, prev=self.det)
        self.det = det
        if not det.found:
            self._stable = 0
            self.message = "Chưa thấy cốc trong khung hình..."
            self._maybe_timeout("khong thay coc")
            return
        moved = (self._prev_box is not None and
                 abs(det.x0 - self._prev_box[0]) + abs(det.y0 - self._prev_box[1]) > 3)
        self._prev_box = (det.x0, det.y0, det.x1, det.y1)
        self._stable = 0 if moved else self._stable + 1
        self.message = "Giữ cốc yên... (%d/%d)" % (self._stable, self.sc.recognize_stable)
        if self._stable < self.sc.recognize_stable:
            return
        self._confirm(det, frame)

    def _confirm(self, det: CupDetection, frame=None) -> None:
        height_mm = float(det.cup_height_mm)
        r_rim = float(det.r_rim_mm)
        r_base = float(det.r_base_mm)
        max_ml = EspBridge.max_ml_from_cup(height_mm, r_base, r_rim)
        water_ml = float(det.volume_ml) if det.waterline_found else 0.0
        room_ml = max(0.0, max_ml - water_ml)
        label = "cup_%dmm" % int(round(height_mm))
        self.bridge.confirm_cup(det, height_mm=height_mm, rim_r_mm=r_rim, base_r_mm=r_base,
                                max_ml=room_ml, label=label)
        self.cup_meta = {
            "height_mm": round(height_mm, 1), "r_rim_mm": round(r_rim, 1),
            "r_base_mm": round(r_base, 1), "capacity_ml": round(max_ml, 0),
            "water_now_ml": round(water_ml, 0), "can_pour_ml": round(room_ml, 0),
            "label": label, "confidence": round(float(det.confidence), 2),
        }
        # model mực nước: đọc xem trong cốc đang có sẵn bao nhiêu nước (nếu dùng được)
        if frame is not None and self.level is not None and self.level.available:
            r = self.level.detect(frame)
            if r is not None:
                self.cup_meta["level_band"] = r.label
                self.cup_meta["level_conf"] = round(float(r.conf), 2)
                self.cup_meta["level_ml"] = round(self.level.ml_now(det, r), 0)
                self.log("[PC] model mực nước: %s -> trong cốc ~%d ml"
                         % (r.text(), round(self.level.ml_now(det, r))))
        self.n_recognized += 1
        self._pending = None
        self.state = "READY"
        self.message = "Đã nhận diện cốc - nút bấm và mic đã mở khoá"
        self._emit("cup_confirmed", self.cup_meta)

    def _maybe_timeout(self, reason: str) -> None:
        if self._pending is None:
            return
        if (time.time() - self._t_pending) < self.sc.recognize_timeout_s:
            return
        self.n_rejected += 1
        self.bridge.reject_cup(P.RejectReason.NOT_FOUND, reason)
        self._pending = None
        self.state = "WAIT_CUP"
        self.message = "Không nhận diện được cốc - nhấc ra rồi đặt lại"

    # ==================================================================
    #  Vòng kín thị giác (chế độ PC điều khiển bơm)
    # ==================================================================
    def _ensure_controller(self, ml: float) -> bool:
        if not self.open_camera():
            return False
        if self.ctl is None:
            self.ctl = FillController(self.cfg, self.cam, self.bridge.remote_pump_adapter,
                                      detector=self.detector, level_detector=self.level)
            # demo bằng camera giả lập: cho nước dâng theo lệnh bơm gửi xuống ESP
            syn = getattr(self.cam, "synthetic", None)
            if syn is not None and self.bridge.simulator is not None:
                self.bridge.remote_pump_adapter.syn_cam = syn
        self.ctl.set_preset(ml)
        self.ctl.start()
        return True

    def _control_tick(self, dt: float) -> None:
        if self.ctl is None:
            return
        tel = self.ctl.tick(dt)
        self.t_last_use = time.time()
        if tel["state"] in ("DONE", State.DONE.value):
            self.bridge.stop_fill(P.StopReason.NORMAL)
            self.last_pc_report = dict(tel.get("report") or {})
            self.log("[PC] vòng kín thị giác xong: %s" % tel.get("report"))
            self.state = "DONE"
            self.message = "Rót xong (PC điều khiển)"
            self.ctl = None
        elif tel["state"] in ("FAULT", State.FAULT.value):
            self.bridge.stop_fill(P.StopReason.PC_REQUEST)
            self.last_pc_report = dict(tel.get("report") or {})
            self.log("[PC] vòng kín thị giác GẶP LỖI: %s" % tel.get("alert"))
            self.error = tel.get("alert", "")
            self.state = "FAULT"
            self.message = "Lỗi khi rót: %s" % tel.get("alert")
            self.ctl = None

    # ==================================================================
    #  Vòng chạy
    # ==================================================================
    def tick(self, dt: float = None) -> None:
        dt = float(dt or (1.0 / max(self.sc.loop_hz, 1.0)))
        self.bridge.tick(dt)
        # PC có được cấu hình điều khiển bơm không?
        if self.bridge.pc_controls_pump:
            if self.ctl is not None:
                self._control_tick(dt)
            elif self.bridge.status.pumping and self.bridge.status.state == int(P.EspState.POURING):
                ml = float(self.bridge.status.target_ml or self.bridge.preset_ml)
                if self._ensure_controller(ml):
                    self.state = "POURING_PC"
                    self.message = "Đang rót (PC điều khiển vòng kín thị giác)"
                    self._emit("pc_loop_start", {"ml": ml})
        # nhận diện khi có cốc mới
        if self._pending is not None:
            self._recognize(dt)
        elif self.state in ("IDLE", "WAIT_CUP", "DONE") and \
                self.bridge.status.cup_present and not self.bridge.status.pc_confirmed:
            self.state = "WAIT_CUP"
        # đóng camera khi rảnh
        if self.camera_open and self.sc.auto_camera and self.ctl is None and \
                (time.time() - self.t_last_use) > self.sc.camera_idle_close_s:
            self.close_camera()
            if self.state in ("READY", "DONE"):
                self.state = "IDLE"
                self.message = "Đã đóng camera (chờ cốc mới)"

    # ==================================================================
    def telemetry(self) -> Dict:
        tel: Dict = {
            "state": self.state,
            "message": self.message,
            "error": self.error,
            "camera_open": self.camera_open,
            "cup_meta": self.cup_meta,
            "n_recognized": self.n_recognized,
            "n_rejected": self.n_rejected,
            "esp": self.bridge.summary,
        }
        if self.level is not None:
            tel["level_model"] = {
                "on": bool(getattr(self.level, "available", False)),
                "weights": self.level.weights,
                "band": (self.level.last.label if self.level.last is not None else ""),
                "conf": (round(float(self.level.last.conf), 3)
                         if self.level.last is not None else 0.0),
                "error": self.level.last_error,
            }
        if self.ctl is not None:
            tel["vision"] = self.ctl.telemetry()
        if self.last_pc_report is not None:
            tel["pc_report"] = self.last_pc_report
        if self.det is not None:
            tel["waterline_found"] = bool(self.det.waterline_found)
            tel["h_mm"] = round(float(self.det.water_height_mm), 2)
        return tel

    @property
    def frame(self):
        return self.ctl.frame if self.ctl is not None else getattr(self.cam, "_prefetched", None)

    def close(self) -> None:
        if self.ctl is not None:
            self.ctl.stop("close")
            self.ctl = None
        self.close_camera()
        self.bridge.close()

    # ---- tiện ích cho UI ---------------------------------------------
    def press_preset(self, index: int) -> None:
        """Chọn mức từ phía PC (bàn phím/giao diện web) rồi bắt đầu rót."""
        presets = self.bridge.status.presets or self.bridge.presets
        index = max(0, min(int(index), len(presets) - 1))
        ml = float(presets[index])
        self.bridge.set_preset(ml, index)
        self.log("[PC] chọn mức %g ml" % ml)

    def start_from_pc(self, ml: Optional[float] = None) -> None:
        ml = float(ml or self.bridge.preset_ml)
        if not self.bridge.status.unlocked:
            self.log("[PC] chưa mở khoá: phải nhận diện được cốc trước (nút bấm/mic bị khoá)")
            return
        self.bridge.start_fill(ml)
        self.log("[PC] ra lệnh rót %g ml" % ml)

    def stop_from_pc(self, reason: int = P.StopReason.PC_REQUEST) -> None:
        if self.ctl is not None:
            self.ctl.stop("PC stop")
            self.ctl = None
        self.bridge.stop_fill(reason)
