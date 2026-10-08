"""Tạo ảnh CỐC TRONG SUỐT giả lập - phục vụ test thuật toán khi chưa có phần cứng.

Mô hình vật lý được mô phỏng (đây là lý do ảnh giả lập rất giống ảnh thật):

  1. Nền sau cốc là một dải sáng (đèn nền / tấm tán sáng). Cốc thuỷ tinh KHÔNG
     chắn sáng mà KHÚC XẠ nó -> phần thân cốc hiện ra như một dải TỐI trên nền
     sáng. Đây là tín hiệu chính để tìm cốc.
  2. Phần cốc CHỨA NƯỚC khúc xạ mạnh hơn (nước + 2 lớp thành cốc) -> tối hơn
     phần cốc chứa không khí. Ranh giới 2 vùng chính là VẠCH NƯỚC.
  3. Mặt nước là một ellipse (do nhìn nghiêng); mép GẦN (cạnh dưới ellipse) là
     thứ camera thấy rõ nhất -> sinh ra sai số lệch thị giác phải hiệu chuẩn.
  4. Thành cốc, đáy cốc tạo viền sáng mỏng; có phản quang dọc thân cốc.
  5. Dòng nước rót xuống, bọt khí, sóng mặt nước, nhiễu cảm biến.

Đơn vị hình học: mm, gốc toạ độ tại TÂM QUANG HỌC của camera, trục y hướng LÊN.
Chuyển sang pixel:  col = cx + x*scale ;  row = cy - y*scale
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

__all__ = ["CupGeometry", "CupScene", "SyntheticCupCamera", "render_scene"]

WATER_N = 1.333
GLASS_N = 1.5


# ---------------------------------------------------------------------------
@dataclass
class CupGeometry:
    """Cốc hình nón cụt, thành mỏng."""

    h_mm: float = 100.0          # chiều cao trong lòng cốc
    r_base_mm: float = 30.0      # bán kính trong đáy
    r_rim_mm: float = 37.0       # bán kính trong miệng
    wall_mm: float = 1.4         # độ dày thành
    base_mm: float = 4.0         # độ dày đáy
    y_base_mm: float = -70.0     # cao độ đáy cốc so với tâm quang học

    @property
    def y_rim_mm(self) -> float:
        return self.y_base_mm + self.h_mm

    def radius_at(self, y_mm) -> float:
        """Bán kính TRONG tại cao độ y (mm), nội suy tuyến tính. Nhận cả ndarray."""
        t = (np.asarray(y_mm, dtype=np.float64) - self.y_base_mm) / max(self.h_mm, 1e-6)
        t = np.clip(t, 0.0, 1.0)
        r = self.r_base_mm + (self.r_rim_mm - self.r_base_mm) * t
        return float(r) if np.ndim(y_mm) == 0 else r

    def area_mm2(self, y_mm: float) -> float:
        r = self.radius_at(y_mm)
        return math.pi * r * r

    def volume_to_height(self, ml: float) -> float:
        """Thể tích (ml = cm^3) -> cao độ mặt nước (mm). Giải ngược hình nón cụt."""
        target = ml * 1000.0  # mm^3
        lo, hi = self.y_base_mm, self.y_rim_mm
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self._volume_below(mid) < target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def height_to_volume_ml(self, y_mm: float) -> float:
        return self._volume_below(y_mm) / 1000.0

    def _volume_below(self, y_mm: float) -> float:
        """Thể tích mm^3 từ đáy tới y (tích phân hình nón cụt, dạng giải tích)."""
        h = min(max(y_mm - self.y_base_mm, 0.0), self.h_mm)
        r0, r1 = self.r_base_mm, self.radius_at(self.y_base_mm + h)
        return math.pi * h * (r0 * r0 + r0 * r1 + r1 * r1) / 3.0


@dataclass
class CupScene:
    """Một cảnh (1 khung hình) đầy đủ tham số, kèm ground-truth."""

    # --- hình học ---
    cup: CupGeometry = field(default_factory=CupGeometry)
    water_y_mm: float = -120.0        # cao độ mặt nước THẬT (tâm ellipse)
    cup_dx_mm: float = 0.0            # cốc đặt lệch ngang
    cup_tilt_deg: float = 0.0         # cốc nghiêng
    camera_dy_mm: float = 0.0         # tâm quang học lệch cao khỏi ... (dùng cho GT)

    # --- quang học / môi trường ---
    scale_px_per_mm: float = 2.4
    strip_x_mm: Tuple[float, float] = (-55.0, 55.0)
    strip_brightness: float = 190.0
    bg_brightness: float = 34.0
    wall_dim: float = 0.30            # thân cốc (không nước) chặn bao nhiêu nền sáng
    water_dim: float = 0.10           # phần chứa nước chặn gần hết nền sáng
    exposure: float = 1.0
    gamma: float = 1.0
    noise_sigma: float = 2.2
    blur_px: float = 0.7
    reflection_gain: float = 0.55     # vệt phản quang dọc thân cốc

    # --- trạng thái động ---
    pouring: bool = False
    stream_x_mm: float = 6.0
    stream_flow: float = 0.0          # 0..1 (chỉ để vẽ to/nhỏ)
    wave_amp_px: float = 0.0
    bubble_level: float = 0.0         # 0..1
    time_s: float = 0.0

    # --- khung ảnh ---
    width: int = 640
    height: int = 480

    # ------------------------------------------------------------------
    @property
    def cx(self) -> float:
        return self.width * 0.5

    @property
    def cy(self) -> float:
        return self.height * 0.5

    def fill_ratio(self) -> float:
        c = self.cup
        return (self.water_y_mm - c.y_base_mm) / max(c.h_mm, 1e-6)

    def volume_ml(self) -> float:
        return self.cup.height_to_volume_ml(self.water_y_mm)

    def to_px(self, x_mm: float, y_mm: float) -> Tuple[float, float]:
        return self.cx + x_mm * self.scale_px_per_mm, self.cy - y_mm * self.scale_px_per_mm

    def meniscus_ry_px(self) -> float:
        """Bán trục đứng (px) của ellipse mặt nước - chính là nguồn sai số lệch thị giác."""
        r = self.cup.radius_at(min(max(self.water_y_mm, self.cup.y_base_mm), self.cup.y_rim_mm))
        return _ellipse_ry(r, self.camera_dy_mm - self.water_y_mm, self.scale_px_per_mm)


def _ellipse_ry(r_mm: float, cam_dy_mm: float, scale: float) -> float:
    """Bán trục đứng ellipse của mặt tròn nhìn từ camera lệch cam_dy_mm.

    Với ống kính tiêu cự f (px) và khoảng cách vật D (mm): ry = f * r / D.
    Ở đây ta coi cam_dy_mm chính là lượng lệch tâm đủ nhỏ để xấp xỉ
    ry ~ r * |cam_dy| / D_eff với D_eff ~ 400 mm (khoảng cách camera-cốc điển hình).
    """
    D_eff = 400.0
    return scale * r_mm * abs(cam_dy_mm) / D_eff


# ---------------------------------------------------------------------------
def render_scene(scene: CupScene, rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """Kết xuất 1 khung hình BGR (uint8) cho scene."""
    rng = rng or np.random.default_rng(1234)
    S = scene.scale_px_per_mm
    cup = scene.cup
    W, H = scene.width, scene.height
    cx, cy = scene.cx, scene.cy

    img = np.zeros((H, W, 3), np.float32)

    # ---------- 1. nền tối ----------
    img[:, :] = scene.bg_brightness * np.array([1.0, 1.02, 1.06], np.float32)

    # ---------- 2. dải đèn nền (có vân dọc nhẹ + mép mờ) ----------
    sx0, _ = scene.to_px(scene.strip_x_mm[0], 0)
    sx1, _ = scene.to_px(scene.strip_x_mm[1], 0)
    cols = np.arange(W, dtype=np.float32)
    edge = 6.0
    strip = np.clip((cols - (sx0 - edge)) / edge, 0, 1) * np.clip(((sx1 + edge) - cols) / edge, 0, 1)
    grain = 1.0 + 0.03 * np.sin(cols * 0.35) + 0.02 * rng.standard_normal(W).astype(np.float32)
    band = (scene.strip_brightness * grain * strip)[:, None]
    img += band * np.array([1.0, 1.0, 0.98], np.float32)

    # ---------- 3. lưới toạ độ theo cup-local ----------
    tilt = math.radians(scene.cup_tilt_deg)
    ct, st = math.cos(tilt), math.sin(tilt)

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    # pixel -> mm (world)
    xw = (xx - cx) / S
    yw = (cy - yy) / S
    # world -> cup local (quay ngược tilt quanh đáy cốc, tịnh tiến dx)
    ox, oy = scene.cup_dx_mm, cup.y_base_mm
    xl = (xw - ox) * ct + (yw - oy) * st
    yl = -(xw - ox) * st + (yw - oy) * ct + oy

    inside = (np.abs(xl) <= (cup.radius_at(np.clip(yl, cup.y_base_mm, cup.y_rim_mm)) + cup.wall_mm)) & (
        yl >= cup.y_base_mm - cup.base_mm
    ) & (yl <= cup.y_rim_mm)
    inner = (np.abs(xl) < cup.radius_at(np.clip(yl, cup.y_base_mm, cup.y_rim_mm))) & (
        yl >= cup.y_base_mm
    ) & (yl <= cup.y_rim_mm)
    wall = inside & ~inner
    base = (np.abs(xl) <= cup.r_base_mm + cup.wall_mm) & (yl < cup.y_base_mm) & (yl >= cup.y_base_mm - cup.base_mm)

    below = yl < scene.water_y_mm  # vùng chứa nước (trong lòng cốc)

    # ---------- 4. khúc xạ: phần cốc lấy nền sáng theo x bị "kéo" ----------
    r_here = cup.radius_at(np.clip(yl, cup.y_base_mm, cup.y_rim_mm)) + 1e-6
    frac = np.clip(xl / r_here, -1, 1)
    # lệch tâm: ảnh nền bị nén về phía thành cốc
    refr_x = xw - 0.55 * cup.wall_mm * np.sign(xl) * (1 - np.abs(frac)) ** 0.5 - 0.35 * frac * r_here * 0.25
    strip_refr = np.clip((refr_x - (scene.strip_x_mm[0] - edge)) / edge, 0, 1) * np.clip(
        ((scene.strip_x_mm[1] + edge) - refr_x) / edge, 0, 1
    )
    bg_val = scene.bg_brightness + scene.strip_brightness * strip_refr

    # thân cốc (không nước): giảm sáng vừa
    dim_air = scene.wall_dim + 0.10 * np.abs(frac) ** 2
    # phần chứa nước: giảm sáng mạnh (khúc xạ 2 lần + hấp thụ)
    dim_water = scene.water_dim * (0.55 + 0.45 * np.abs(frac))

    transmit = np.ones_like(bg_val)
    transmit[inner & ~below] = dim_air[inner & ~below]
    transmit[inner & below] = dim_water[inner & below]
    transmit[wall] = np.where(below[wall], 0.42, 0.62)
    transmit[base] = 0.30

    refracted = bg_val * transmit

    # ---------- 5. vệt phản quang dọc thân cốc ----------
    refl = np.zeros_like(bg_val)
    d_left = np.abs(xl + r_here * 0.72)
    d_right = np.abs(xl - r_here * 0.78)
    refl = scene.reflection_gain * (
        np.exp(-(d_left / 1.6) ** 2) + 0.7 * np.exp(-(d_right / 1.2) ** 2)
    )
    refl[~inside] = 0.0
    refl[below] *= 0.35

    cup_pixels = np.where(inside | base, refracted, bg_val) + refl * scene.strip_brightness / 190.0

    img[:, :, 0] = cup_pixels * 1.0
    img[:, :, 1] = cup_pixels * 1.0
    img[:, :, 2] = cup_pixels * 0.985

    # ---------- 6. vạch nước (meniscus) ----------
    if scene.water_y_mm > cup.y_base_mm - 1:
        ry_mm = _ellipse_ry(
            cup.radius_at(min(max(scene.water_y_mm, cup.y_base_mm), cup.y_rim_mm)),
            scene.camera_dy_mm - scene.water_y_mm,
            1.0,
        )
        ry = max(ry_mm * S, 0.6)
        # sóng mặt nước
        wave = scene.wave_amp_px * np.sin(xl * 0.55 + scene.time_s * 9.0)
        wline_row = cy - (scene.water_y_mm * S) + wave  # hàng của TÂM ellipse (px)

        # ellipse: (x_local_px / rx)^2 + ((row - wline_row)/ry)^2 = 1
        xl_px = (xl - 0.0) * S
        rx = max(cup.radius_at(scene.water_y_mm) * S, 1.0)
        ell = (xl_px / rx) ** 2 + ((yy - wline_row) / ry) ** 2

        # mép GẦN (nửa dưới ellipse) = vạch sáng mạnh
        near_band = (ell < 1.0) & (yy > wline_row - 0.5)
        far_band = (ell < 1.0) & (yy <= wline_row + 0.5)
        edge_ring = (np.abs(np.sqrt(np.clip(ell, 0, None)) - 1.0) < (1.9 / max(rx, ry))) & inner

        img[:, :, 0][near_band] += 26.0
        img[:, :, 1][near_band] += 27.0
        img[:, :, 2][near_band] += 24.0
        img[:, :, 0][far_band] -= 8.0
        img[:, :, 1][far_band] -= 8.0
        img[:, :, 2][far_band] -= 7.0
        img[edge_ring] += np.array([58.0, 60.0, 54.0], np.float32) * scene.exposure

        # 2 góc meniscus bám thành cốc (điểm sáng rất đặc trưng)
        for sgn in (-1, 1):
            gx, gy = scene.to_px(
                scene.cup_dx_mm + sgn * cup.radius_at(scene.water_y_mm) * ct,
                scene.water_y_mm + sgn * cup.radius_at(scene.water_y_mm) * st,
            )
            gx_i, gy_i = int(round(gx)), int(round(gy))
            if 4 < gx_i < W - 4 and 4 < gy_i < H - 4:
                img[gy_i - 3 : gy_i + 4, gx_i - 3 : gx_i + 4] += np.array(
                    [70.0, 72.0, 66.0], np.float32
                )

    # ---------- 7. viền thành cốc + miệng cốc ----------
    rim_mask = (np.abs(yl - cup.y_rim_mm) < 0.9) & (np.abs(xl) <= cup.r_rim_mm + cup.wall_mm)
    img[rim_mask] += np.array([46.0, 46.0, 44.0], np.float32)

    outline = cv2.Canny((np.clip(img, 0, 255).astype(np.uint8)), 60, 150) > 0
    outline &= (wall | base)
    img[outline] += 22.0

    # ---------- 8. bọt khí ----------
    if scene.bubble_level > 0 and scene.water_y_mm > cup.y_base_mm:
        n = int(scene.bubble_level * 140)
        for _ in range(n):
            t = rng.random()
            by = scene.water_y_mm - t * (scene.water_y_mm - cup.y_base_mm) * (0.25 + 0.75 * t)
            bx = scene.cup_dx_mm + (rng.random() * 2 - 1) * cup.radius_at(by) * 0.88
            px, py = scene.to_px(bx, by)
            rad = float(rng.integers(1, 4))
            if 2 < px < W - 2 and 2 < py < H - 2:
                cv2.circle(img, (int(px), int(py)), int(rad), (48, 50, 46), -1, cv2.LINE_AA)

    # ---------- 9. dòng nước đang rót ----------
    if scene.pouring and scene.water_y_mm < cup.y_rim_mm:
        nozzle_y_mm = cup.y_rim_mm + 55.0
        nx, ny0 = scene.to_px(scene.stream_x_mm + scene.cup_dx_mm, nozzle_y_mm)
        _, ny1 = scene.to_px(0, max(scene.water_y_mm, cup.y_base_mm))
        width = 2.0 + 4.0 * scene.stream_flow
        for row in range(int(min(ny0, ny1)), int(max(ny0, ny1))):
            if not (0 <= row < H):
                continue
            wob = 0.8 * math.sin(row * 0.11 + scene.time_s * 22.0)
            c0 = int(nx + wob - width / 2)
            c1 = int(nx + wob + width / 2)
            c0, c1 = max(0, c0), min(W, c1)
            if c1 > c0:
                img[row, c0:c1] = np.array([168.0, 176.0, 170.0], np.float32)
        # vùng bắn toé
        splash_h = int(4 + 7 * scene.stream_flow)
        sy = int(min(max(ny1, 0), H - 1))
        sx_c = int(min(max(nx, 0), W - 1))
        img[max(0, sy - splash_h) : sy + 1, max(0, sx_c - 9) : sx_c + 9] += np.array(
            [40.0, 42.0, 38.0], np.float32
        )

    # ---------- 10. phơi sáng / gamma / nhiễu / mờ ----------
    img *= scene.exposure
    img = np.clip(img, 0, 255)
    if abs(scene.gamma - 1.0) > 1e-3:
        img = 255.0 * (img / 255.0) ** (1.0 / max(scene.gamma, 1e-3))
    img = np.clip(img, 0, 255).astype(np.uint8)
    if scene.noise_sigma > 0:
        noise = rng.normal(0, scene.noise_sigma, img.shape).astype(np.float32)
        img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if scene.blur_px > 0.05:
        k = int(2 * round(scene.blur_px * 1.6) + 1)
        img = cv2.GaussianBlur(img, (k, k), scene.blur_px)
    return img


# ---------------------------------------------------------------------------
class SyntheticCupCamera:
    """Nguồn ảnh giả lập có cùng interface với camera thật (read()).

    Tự đóng vai cả "vật lý": bơm chạy thì mực nước dâng lên theo thể tích,
    có trễ pha, có sóng và bọt -> dùng để chạy thử controller end-to-end.
    """

    def __init__(self, scene: Optional[CupScene] = None, seed: int = 7, fps: float = 30.0):
        self.scene = scene or CupScene()
        self.rng = np.random.default_rng(seed)
        self.dt = 1.0 / fps
        self._t = 0.0
        self._ml = max(self.scene.cup.height_to_volume_ml(self.scene.water_y_mm), 0.0)
        self._flow_ml_s = 0.0          # lưu lượng thực tế đang vào cốc
        self._target_flow = 0.0        # lưu lượng bơm đặt
        self._in_flight_ml = 0.0
        self.pump_lag_s = 0.45

    # ---- API giống camera thật --------------------------------------
    def read(self) -> Tuple[bool, np.ndarray]:
        self.step(self.dt)
        return True, render_scene(self.scene, self.rng)

    def release(self) -> None:
        pass

    # ---- API giả lập vật lý -----------------------------------------
    def set_flow(self, ml_per_s: float) -> None:
        """Controller gọi: đặt lưu lượng bơm mong muốn."""
        self._target_flow = max(0.0, float(ml_per_s))
        self.scene.pouring = self._target_flow > 0.5
        self.scene.stream_flow = min(self._target_flow / 40.0, 1.0)

    def step(self, dt: float) -> None:
        self._t += dt
        self.scene.time_s = self._t
        # trễ pha bậc 1 của bơm + ống
        alpha = dt / max(self.pump_lag_s, dt)
        self._flow_ml_s += (self._target_flow - self._flow_ml_s) * min(alpha, 1.0)
        self.scene.pouring = self._flow_ml_s > 0.5
        self.scene.stream_flow = min(self._flow_ml_s / 40.0, 1.0)

        # nước đang bay: trễ vận chuyển 90 ms
        delay_in = self._flow_ml_s * dt
        self._in_flight_ml += delay_in
        arrive = self._in_flight_ml * min(dt / 0.09, 1.0)
        self._in_flight_ml -= arrive

        cup = self.scene.cup
        self._ml = min(max(self._ml + arrive, 0.0), cup.height_to_volume_ml(cup.y_rim_mm) * 1.02)
        self.scene.water_y_mm = cup.volume_to_height(self._ml) if self._ml > 0 else cup.y_base_mm

        # sóng & bọt phụ thuộc dòng rót
        self.scene.wave_amp_px = min(self._flow_ml_s * 0.055, 2.2)
        self.scene.bubble_level = min(self._flow_ml_s / 45.0, 0.85) * (1.0 if self._flow_ml_s > 1 else 0.0)

    # ---- tiện ích ----------------------------------------------------
    @property
    def volume_ml(self) -> float:
        return self._ml

    @property
    def true_height_mm(self) -> float:
        return self.scene.water_y_mm - self.scene.cup.y_base_mm

    def place_cup(self, geom: Optional[CupGeometry] = None, dx_mm: float = 0.0) -> None:
        self.scene.cup = geom or CupGeometry()
        self.scene.cup_dx_mm = dx_mm
        self._ml = 0.0
        self.scene.water_y_mm = self.scene.cup.y_base_mm

    def remove_cup(self) -> None:
        self.scene.cup = CupGeometry(h_mm=0.001, r_base_mm=0.001, r_rim_mm=0.001, y_base_mm=-999)
        self._ml = 0.0


# ---------------------------------------------------------------------------
def make_test_set(
    n: int = 60, seed: int = 0, width: int = 640, height: int = 480, jitter: bool = True
) -> list:
    """Sinh n (scene, image, ground_truth) phủ nhiều loại cốc / mức nước / nhiễu."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        # chọn tỉ lệ px/mm trước để đảm bảo cốc lọt trọn khung hình
        scale = float(rng.uniform(1.6, 2.3))
        bound = 226.0 / scale          # |y| tối đa (mm) để cốc không chạm mép ảnh
        h_mm = float(rng.uniform(60, min(130, (bound - 8) / 0.66)))
        y_base = -h_mm * float(rng.uniform(0.35, 0.65))
        cup = CupGeometry(
            h_mm=h_mm,
            r_base_mm=float(rng.uniform(24, 36)),
            r_rim_mm=float(rng.uniform(32, 48)),
            wall_mm=float(rng.uniform(0.9, 2.2)),
            y_base_mm=y_base,
        )
        cup.r_rim_mm = max(cup.r_rim_mm, cup.r_base_mm + 3)
        fill = float(rng.uniform(0.0, 0.95))
        dx = float(rng.uniform(-10, 10)) if jitter else 0.0
        water_y = cup.y_base_mm + fill * cup.h_mm
        cam_dy = float(rng.uniform(-40, 40)) if jitter else 0.0
        scene = CupScene(
            cup=cup,
            water_y_mm=water_y,
            camera_dy_mm=cam_dy,
            cup_dx_mm=dx,
            cup_tilt_deg=float(rng.uniform(-1.6, 1.6)) if jitter else 0.0,
            scale_px_per_mm=scale,
            strip_brightness=float(rng.uniform(150, 215)),
            bg_brightness=float(rng.uniform(22, 48)),
            wall_dim=float(rng.uniform(0.22, 0.42)),
            water_dim=float(rng.uniform(0.06, 0.16)),
            noise_sigma=float(rng.uniform(0.8, 4.5)),
            blur_px=float(rng.uniform(0.4, 1.2)),
            exposure=float(rng.uniform(0.85, 1.15)),
            reflection_gain=float(rng.uniform(0.2, 0.8)),
            strip_x_mm=(float(-(cup.r_rim_mm + abs(dx) + 7)), float(cup.r_rim_mm + abs(dx) + 7)),
            width=width,
            height=height,
        )
        img = render_scene(scene, rng)
        ry_px = _ellipse_ry(cup.radius_at(water_y), cam_dy - water_y, scene.scale_px_per_mm)
        gt = {
            "water_y_mm": water_y,
            "height_mm": water_y - cup.y_base_mm,
            "fill_ratio": fill,
            "volume_ml": cup.height_to_volume_ml(water_y),
            "y_rim_mm": cup.y_rim_mm,
            "y_base_mm": cup.y_base_mm,
            "r_rim_mm": cup.r_rim_mm,
            "r_base_mm": cup.r_base_mm,
            "h_mm": cup.h_mm,
            "scale": scene.scale_px_per_mm,
            "cup_dx_mm": scene.cup_dx_mm,
            "camera_dy_mm": cam_dy,
            "near_edge_y_mm": water_y - ry_px / scene.scale_px_per_mm,  # mép gần (camera thấy)
        }
        out.append((scene, img, gt))
    return out
