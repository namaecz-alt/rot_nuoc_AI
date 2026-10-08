"""Nhận diện CỐC TRONG SUỐT và đo MỰC NƯỚC bằng thị giác cổ điển (OpenCV).

Vì sao không dùng màu/texture: cốc thuỷ tinh trong suốt, gần như vô hình trên nền
thường. Trick chuẩn công nghiệp là CHIẾU SÁNG NGƯỢC (backlight): đặt một dải đèn
sáng PHÍA SAU cốc. Khi đó:

  * Thân cốc (không nước)  = dải TỐI vừa   (thành cốc khúc xạ nền sáng ra ngoài)
  * Phần cốc chứa nước      = dải TỐI đậm   (nước khúc xạ mạnh hơn nữa)
  * Vạch nước               = biên ngang sắc nét giữa hai mức tối đó
  * Miệng cốc / đáy cốc     = điểm đầu cuối của dải tối

Pipeline (mọi thứ O(W*H), chạy ~10-20 fps trên Raspberry Pi 4):

  1. find_strip   : dò dải nền sáng theo profile cột
  2. find_cup     : trong dải sáng, tìm "bóng tối" của cốc theo profile hàng
                    -> rim_y, base_y, mặt nạ cốc
  3. fit_walls    : hồi quy 2 thành cốc -> bán kính trong theo chiều cao
  4. find_waterline: với mỗi cột trong lòng cốc, tìm biên ngang mạnh nhất
                    (gradient dọc âm: sáng-trên / tối-dưới) -> RANSAC đường thẳng
  5. đổi pixel -> mm bằng mm_per_px đã hiệu chuẩn, bù lệch thị giác (meniscus)

Kết quả trả về CupDetection (dataclass) kèm dict debug để vẽ overlay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

__all__ = ["CupDetection", "CupDetector", "WaterTracker", "cone_volume_ml"]


# ---------------------------------------------------------------------------
@dataclass
class CupDetection:
    found: bool = False
    # hộp bao cốc (px)
    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0
    rim_y_px: float = 0.0          # hàng pixel miệng cốc
    base_y_px: float = 0.0         # hàng pixel đáy TRONG lòng cốc (mức 0 mm)
    # hệ số thành cốc: x = a*row + b (px)
    left_wall: Tuple[float, float] = (0.0, 0.0)
    right_wall: Tuple[float, float] = (0.0, 0.0)
    r_rim_mm: float = 0.0          # bán kính trong tại miệng
    r_base_mm: float = 0.0         # bán kính trong tại đáy
    cup_height_mm: float = 0.0

    waterline_found: bool = False
    waterline_y_px: float = 0.0    # hàng pixel vạch nước (mép gần meniscus)
    water_height_mm: float = 0.0   # chiều cao cột nước tính từ đáy trong
    fill_ratio: float = 0.0        # 0..1 theo chiều cao
    volume_ml: float = 0.0         # ước lượng thể tích từ hình nón cụt đo được

    confidence: float = 0.0
    mm_per_px: float = 0.0
    debug: Dict[str, np.ndarray] = field(default_factory=dict)

    def width_at(self, row: float) -> float:
        """Bề rộng trong (px) của cốc tại hàng `row`."""
        return (self.right_wall[0] * row + self.right_wall[1]) - (
            self.left_wall[0] * row + self.left_wall[1]
        )


def cone_volume_ml(h_mm: float, r_base_mm: float, r_top_mm: float) -> float:
    """Thể tích (ml) hình nón cụt cao h, bán kính đáy r_base, bán kính mặt r_top."""
    if h_mm <= 0:
        return 0.0
    return float(np.pi * h_mm * (r_base_mm ** 2 + r_base_mm * r_top_mm + r_top_mm ** 2) / 3.0 / 1000.0)


def _cone_volume_up_to(det: "CupDetection", h_mm: float) -> float:
    """Thể tích nước trong cốc đo được, với cột nước cao h_mm."""
    h_mm = float(np.clip(h_mm, 0.0, max(det.cup_height_mm, 1e-6)))
    t = h_mm / max(det.cup_height_mm, 1e-6)
    r_top = det.r_base_mm + (det.r_rim_mm - det.r_base_mm) * t
    return cone_volume_ml(h_mm, det.r_base_mm, r_top)


# ---------------------------------------------------------------------------
class WaterTracker:
    """Lọc thời gian: trung vị cửa sổ + chặn bước nhảy PHI VẬT LÝ.

    Chặn dâng: mực nước không thể dâng nhanh hơn lưu lượng bơm cực đại, nhưng
    giới hạn phải tính theo KHOẢNG THỜI GIAN từ lần chấp nhận gần nhất (chứ không
    theo dt từng khung) - nếu không, sau vài khung mất vạch nước, giá trị đúng sẽ
    bị từ chối vĩnh viễn vì "nhảy quá xa".
    Chặn tụt: nước trong cốc không tự tụt -> chỉ cho phép tụt <= 2 mm (nhiễu).
    """

    def __init__(self, window: int = 5, max_rise_mm_per_s: float = 45.0):
        self.window = max(int(window), 1)
        self.max_rise = max_rise_mm_per_s
        self._buf: List[float] = []
        self._last: Optional[float] = None
        self._t = 0.0
        self._t_acc = 0.0

    def reset(self) -> None:
        self._buf.clear()
        self._last = None
        self._t = 0.0
        self._t_acc = 0.0

    def update(self, h_mm: Optional[float], dt: float) -> Optional[float]:
        self._t += dt if dt > 0 else 0.0
        if h_mm is None:
            return self._last
        if self._last is not None:
            gap = max(self._t - self._t_acc, dt)
            hi = self._last + self.max_rise * gap
            lo = self._last - 2.0
            if not (lo <= h_mm <= hi):
                return self._last
        self._buf.append(float(h_mm))
        if len(self._buf) > self.window:
            self._buf.pop(0)
        self._last = float(np.median(self._buf))
        self._t_acc = self._t
        return self._last


# ---------------------------------------------------------------------------
class CupDetector:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        d = cfg.get("detection", {}) if hasattr(cfg, "get") else cfg
        strip = d.get("strip", {})
        cup = d.get("cup", {})
        wl = d.get("waterline", {})
        cal = cfg.get("calibration", {}) if hasattr(cfg, "get") else {}

        self.roi = d.get("cup_roi", None)
        self.strip_auto = bool(strip.get("auto_find", True))
        self.strip_x_range = tuple(strip.get("x_range", (0, 10 ** 6)))
        self.strip_min_bright = float(strip.get("min_brightness", 150))
        self.strip_w = (int(strip.get("min_width_px", 40)), int(strip.get("max_width_px", 320)))

        self.cup_w = (int(cup.get("min_width_px", 28)), int(cup.get("max_width_px", 260)))
        self.cup_h = (int(cup.get("min_height_px", 45)), int(cup.get("max_height_px", 420)))
        self.cup_fill_ratio = float(cup.get("min_fill_ratio", 0.35))
        self.cup_merge_gap = int(cup.get("merge_gap_px", 28))

        self.wl_thr = float(wl.get("grad_threshold", 14))
        self.wl_support = float(wl.get("min_support_ratio", 0.45))
        self.wl_margin = float(wl.get("search_margin_ratio", 0.92))
        self.wl_ransac = float(wl.get("ransac_thresh_px", 2.5))

        self.mm_per_px = float(cal.get("mm_per_px", 0.417))
        self.ocx, self.ocy = cal.get("optical_center_px", (320.0, 240.0))
        self.meniscus_off = float(cal.get("meniscus_offset_mm", 0.0))
        self.cam_off = float(cal.get("camera_offset_mm", 0.0))
        self.wall_mm = float(cup.get("wall_mm", 1.5))
        self.base_mm = float(cup.get("base_mm", 4.0))

        self._strip_cache: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------------
    def detect(self, bgr: np.ndarray, prev: Optional[CupDetection] = None,
               debug: bool = False) -> CupDetection:
        gray = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        res = CupDetection()

        # ---------- 1. dải nền sáng ----------
        sx0, sx1 = self._find_strip(gray)
        if sx0 is None:
            res.debug = {"gray": gray}
            return res
        res.debug["strip"] = (sx0, sx1)
        # xén mép mờ của dải sáng (blur lan ~5px) để không dính nền tối hai bên
        mx = 8
        if sx1 - sx0 - 2 * mx < self.strip_w[0]:
            return res
        sx0, sx1 = sx0 + mx, sx1 - mx

        # ---------- 2. bóng cốc trong dải sáng ----------
        # 2a. ngưỡng "tối": giữa mức nền sáng và mức thân cốc
        strip_med = float(np.median(np.percentile(gray[::3, sx0 : sx1 + 1].astype(np.float32), 80, axis=0)))
        thr_dark = 0.75 * strip_med

        # 2b. tìm CỘT của cốc: cột nào có nhiều pixel tối dọc theo chiều cao
        col_dark = (gray[:, sx0 : sx1 + 1] < thr_dark).mean(axis=0)
        col_dark = cv2.GaussianBlur(col_dark.reshape(1, -1), (5, 1), 0).ravel()
        col_runs = self._runs(col_dark > 0.5 * max(float(col_dark.max()), 1e-6),
                              min_len=6, max_len=self.cup_w[1])
        if not col_runs:
            return res
        # nối các dải cột bị CHIỀU NƯỚC RÓT / vệt sáng chém ngang (gap <= 28px)
        col_runs = self._merge_runs(col_runs, gap=self.cup_merge_gap)
        col_runs = [r for r in col_runs if self.cup_w[0] <= r[1] - r[0] <= self.cup_w[1]]
        if not col_runs:
            return res
        c0, c1 = max(col_runs, key=lambda t: t[1] - t[0])
        col_lo, col_hi = sx0 + c0, sx0 + c1 + 1

        # 2c. tìm HÀNG của cốc trong cửa sổ cột đó
        sub = gray[:, col_lo:col_hi].astype(np.float32)
        row_dark_frac = (sub < thr_dark).mean(axis=1)
        row_dark_frac = cv2.GaussianBlur(row_dark_frac.reshape(-1, 1), (1, 5), 0).ravel()
        rows = self._runs(row_dark_frac > 0.6, min_len=self.cup_h[0], max_len=self.cup_h[1])
        if not rows:
            return res
        r0, r1 = max(rows, key=lambda t: (t[1] - t[0]))
        if r1 - r0 < self.cup_h[0]:
            return res

        # mặt nạ cốc: pixel tối trong hộp [r0..r1] x [cột cốc]
        band = gray[r0 : r1 + 1, col_lo:col_hi]
        mask = (band < thr_dark).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 5), np.uint8))
        if mask.mean() < self.cup_fill_ratio:
            return res

        # biên trái/phải từng hàng
        n_row = mask.shape[0]
        left = np.full(n_row, -1, np.int32)
        right = np.full(n_row, -1, np.int32)
        idx = np.arange(n_row)
        any_m = mask.any(axis=1)
        left[any_m] = np.argmax(mask[any_m], axis=1)
        right[any_m] = mask.shape[1] - 1 - np.argmax(mask[any_m][:, ::-1], axis=1)
        width = (right - left + 1) * any_m
        w_med = float(np.median(width[width > 0])) if (width > 0).any() else 0.0
        if not (self.cup_w[0] <= w_med <= self.cup_w[1]):
            return res

        # tinh chỉnh rim/base: lấy dải hàng liên tục DÀI NHẤT có width hợp lệ
        ok = (width >= 0.55 * w_med) & (width <= 1.6 * w_med)
        ok_idx = np.flatnonzero(ok)
        if ok_idx.size == 0:
            return res
        runs_ok: List[np.ndarray] = []
        start = 0
        for i in range(1, ok_idx.size):
            if ok_idx[i] - ok_idx[i - 1] > 2:
                runs_ok.append(ok_idx[start:i])
                start = i
        runs_ok.append(ok_idx[start:])
        main = max(runs_ok, key=len)
        r0, r1 = int(r0 + main[0]), int(r0 + main[-1])
        # CĂN LẠI chỉ số mặt nạ / biên trái phải theo dải hàng mới
        mask = mask[main[0] : main[-1] + 1]
        left = left[main[0] : main[-1] + 1]
        right = right[main[0] : main[-1] + 1]
        n_row = mask.shape[0]

        res.found = True
        res.y0, res.y1 = r0, r1
        res.rim_y_px = r0 + 0.5
        res.base_y_px = r1 + 0.5 - self.base_mm / self.mm_per_px  # đáy TRONG lòng cốc

        # ---------- 3. hồi quy thành cốc ----------
        rows_fit = np.arange(r0 + max(3, int(0.06 * (r1 - r0))), r1 - max(2, int(0.05 * (r1 - r0))))
        lf = left[rows_fit - r0].astype(np.float64) + col_lo
        rf = right[rows_fit - r0].astype(np.float64) + col_lo
        good = (lf >= 0) & (rf > lf)
        if good.sum() < 8:
            res.found = False
            return res
        yy = rows_fit[good].astype(np.float64)
        al, bl = np.polyfit(yy, lf[good], 1)
        ar, br = np.polyfit(yy, rf[good], 1)
        # trừ bề dày thành (px) để lấy bán kính TRONG
        wall_px = self.wall_mm / self.mm_per_px
        res.left_wall = (float(al), float(bl) + wall_px)
        res.right_wall = (float(ar), float(br) - wall_px)

        h_px = (res.base_y_px - res.rim_y_px)
        res.cup_height_mm = h_px * self.mm_per_px
        # bán kính đo TRỰC TIẾP bằng trung vị bề rộng mặt nạ sát miệng/đáy
        # (không ngoại suy từ đường thành -> không khuếch đại sai số với cốc cao)
        wr = [
            right[i] - left[i] + 1
            for i in range(2, min(9, n_row - 1))
            if left[i] >= 0 and right[i] > left[i]
        ]
        wb = [
            right[i] - left[i] + 1
            for i in range(max(0, n_row - 8), max(1, n_row - 2))
            if left[i] >= 0 and right[i] > left[i]
        ]
        if wr:
            res.r_rim_mm = max(0.5 * float(np.median(wr)) * self.mm_per_px - self.wall_mm, 4.0)
        if wb:
            res.r_base_mm = max(0.5 * float(np.median(wb)) * self.mm_per_px - self.wall_mm, 4.0)
        res.x0 = int(round(res.left_wall[0] * r0 + res.left_wall[1]))
        res.x1 = int(round(res.right_wall[0] * r0 + res.right_wall[1]))
        res.mm_per_px = self.mm_per_px
        res.confidence = float(min(1.0, mask.mean() / max(self.cup_fill_ratio, 1e-3)))

        # ---------- 4. vạch nước ----------
        wl = self._find_waterline(gray, res, prev, debug)
        if wl is not None:
            res.waterline_found = True
            res.waterline_y_px = wl
            h_mm = (res.base_y_px - wl) * self.mm_per_px
            res.water_height_mm = max(0.0, h_mm - self.meniscus_off)
            res.fill_ratio = float(np.clip(res.water_height_mm / max(res.cup_height_mm, 1e-6), 0, 1))
            res.volume_ml = _cone_volume_up_to(res, res.water_height_mm)

        if debug:
            res.debug.update(
                gray=gray,
                cup_mask=np.pad(mask, ((r0, H - r1 - 1), (col_lo, W - col_hi - 1))),
                waterline=res.waterline_y_px,
            )
        return res

    # ------------------------------------------------------------------
    def _find_strip(self, gray: np.ndarray) -> Tuple[Optional[int], Optional[int]]:
        """Dò dải nền sáng. Dùng percentile-80 theo cột: cốc chỉ che một PHẦN hàng
        của dải sáng nên percentile cao vẫn giữ đúng mức sáng của đèn nền."""
        W = gray.shape[1]
        x0r, x1r = int(np.clip(self.strip_x_range[0], 0, W)), int(np.clip(self.strip_x_range[1], 0, W))
        prof = np.percentile(gray[::3, x0r:x1r].astype(np.float32), 80, axis=0)
        prof = cv2.GaussianBlur(prof.reshape(1, -1), (9, 1), 0).ravel()
        thr = max(self.strip_min_bright, 0.5 * (float(prof.max()) + float(prof.min())))
        runs = self._runs(prof > thr, min_len=self.strip_w[0], max_len=self.strip_w[1])
        if not runs:
            return None, None
        a, b = max(runs, key=lambda t: t[1] - t[0])
        return x0r + a, x0r + b

    @staticmethod
    def _merge_runs(runs: List[Tuple[int, int]], gap: int = 28) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for a, b in sorted(runs):
            if out and a - out[-1][1] - 1 <= gap:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out

    @staticmethod
    def _runs(mask1d: np.ndarray, min_len: int = 1, max_len: int = 10 ** 9) -> List[Tuple[int, int]]:
        m = mask1d.astype(bool)
        if not m.any():
            return []
        d = np.diff(np.concatenate(([0], m.view(np.int8), [0])))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        out = [(int(s), int(e - 1)) for s, e in zip(starts, ends) if min_len <= e - s <= max_len]
        return out

    # ------------------------------------------------------------------
    def _find_waterline(self, gray: np.ndarray, cup: CupDetection,
                        prev: Optional[CupDetection], debug: bool) -> Optional[float]:
        """Biên ngang sáng->tối mạnh nhất trong lòng cốc = mép gần meniscus."""
        diag = cup.debug.setdefault("wl_diag", {})
        r0 = int(cup.rim_y_px)
        r1 = int(cup.base_y_px)
        if r1 - r0 < 12:
            return None
        # dải tìm: bỏ margin sát miệng (phản quang) và sát đáy
        top = r0 + max(2, int((1.0 - self.wl_margin) * (r1 - r0)))
        bot = r1 - 3

        # cửa sổ tìm theo vết cũ (nếu có) để chống bọt khí
        if prev is not None and prev.waterline_found:
            p = prev.waterline_y_px
            lo, hi = int(p - 0.35 * (r1 - r0)), int(p + 0.35 * (r1 - r0))
            top, bot = max(top, lo), min(bot, hi)
        if bot - top < 6:
            top, bot = r0 + 2, r1 - 3

        xs_l = cup.left_wall[0] * np.arange(gray.shape[0]) + cup.left_wall[1]
        xs_r = cup.right_wall[0] * np.arange(gray.shape[0]) + cup.right_wall[1]

        sob = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
        # với mỗi CỘT trong lòng cốc: hàng có gradient dọc âm mạnh nhất = biên候选
        cl = int(np.ceil(xs_l[bot])) + 3
        cr = int(np.floor(xs_r[bot])) - 3
        if cr - cl < 6:
            return None
        seg = sob[top : bot + 1, cl : cr + 1]
        idx = np.argmin(seg, axis=0)
        vals = seg[idx, np.arange(seg.shape[1])]
        okc = vals < -self.wl_thr
        cand_rows = (top + idx[okc]).tolist()
        cand_cols = (np.arange(cl, cr + 1)[okc]).tolist()
        cand_mag = (-vals[okc]).tolist()
        if len(cand_rows) < 6:
            return None
        rows = np.asarray(cand_rows, np.float64)
        cols = np.asarray(cand_cols, np.float64)
        mag = np.asarray(cand_mag, np.float64)
        # loại biên yếu (bọt khí, vân nhiễu): ngưỡng theo percentile (bền với
        # ngoại lệ kiểu cột nước rót/vệt sáng có gradient cực lớn)
        keep = mag >= max(self.wl_thr, 0.5 * float(np.percentile(mag, 90)))
        diag["n_cand"] = int(len(rows))
        diag["n_after_mag"] = int(keep.sum())
        rows, cols, mag = rows[keep], cols[keep], mag[keep]
        if len(rows) < 6:
            diag["fail"] = "too_few_after_mag"
            return None
        # loại MÉP XA của ellipse: phía dưới vạch nước thật phải là THÂN NƯỚC tối
        p10, p90 = np.percentile(gray[r0:r1, cup.x0 : cup.x1], [10, 90])
        # phía dưới vạch nước thật phải tối GẦN MỨC NƯỚC (p10) hơn mức không khí
        thr_below = float(p10) + 0.30 * (float(p90) - float(p10))
        H_, W_ = gray.shape
        sel = np.array(
            [
                gray[min(rr + 2, H_ - 1) : min(rr + 7, H_), max(cc - 1, 0) : cc + 2].mean()
                < thr_below
                for rr, cc in zip(rows.astype(int), cols.astype(int))
            ]
        )
        diag["n_after_dark"] = int(sel.sum())
        rows, cols, mag = rows[sel], cols[sel], mag[sel]
        if len(rows) < 5:
            diag["fail"] = "too_few_after_dark"
            return None

        # ---- khớp CUNG ELLIPSE mép gần:  row(x) = yc + ry*sqrt(1-u^2)
        # Tuyến tính theo 2 ẩn (yc, ry). yc = mặt phẳng nước TẠI TRỤC CỐC nên
        # miễn nhiễm lệch thị giác khi camera không đặt ngang mặt nước.
        cx = 0.5 * (cup.x0 + cup.x1)
        rx = max(0.5 * cup.width_at(0.5 * (top + bot)), 4.0)
        u = np.clip((cols - cx) / rx, -1, 1)
        svec = np.sqrt(np.clip(1 - u * u, 0, 1))

        best_inl, best_par, best_cnt = None, None, 0
        rng = np.random.default_rng(0)
        n = len(rows)
        for _ in range(140):
            i, j = rng.integers(0, n, 2)
            ds = svec[i] - svec[j]
            if abs(ds) < 0.15:
                continue
            ry = (rows[i] - rows[j]) / ds
            if not (0.0 <= ry <= 30.0):
                continue
            yc = rows[i] - ry * svec[i]
            inl = np.abs(rows - (yc + ry * svec)) < self.wl_ransac
            cnt = int(inl.sum())
            if cnt > best_cnt:
                best_cnt, best_inl, best_par = cnt, inl, (yc, ry)
        diag["support"] = float(best_inl.mean()) if best_inl is not None else 0.0
        if best_inl is None or best_inl.mean() < self.wl_support:
            diag["fail"] = "low_support"
            return None
        A = np.stack([np.ones(int(best_inl.sum())), svec[best_inl]], axis=1)
        sol, *_ = np.linalg.lstsq(A, rows[best_inl], rcond=None)
        yc, ry = float(sol[0]), float(sol[1])
        # vòng 2: cập nhật bán kính ngang tại đúng cao độ vạch nước rồi khớp lại
        rx2 = max(0.5 * cup.width_at(yc), 4.0)
        u2 = np.clip((cols - cx) / rx2, -1, 1)
        s2 = np.sqrt(np.clip(1 - u2 * u2, 0, 1))
        inl2 = np.abs(rows - (yc + ry * s2)) < self.wl_ransac
        if inl2.sum() >= 5:
            A2 = np.stack([np.ones(int(inl2.sum())), s2[inl2]], axis=1)
            sol2, *_ = np.linalg.lstsq(A2, rows[inl2], rcond=None)
            yc, ry = float(sol2[0]), float(sol2[1])
        if not (r0 + 1 < yc < r1 + 6):
            diag["fail"] = "yc_out_of_cup"
            return None
        cup.debug["meniscus_ry_px"] = ry
        cup.debug["wl_inliers"] = float(best_inl.mean())
        return float(yc)


# ---------------------------------------------------------------------------
def draw_overlay(bgr: np.ndarray, det: CupDetection, target_mm: Optional[float] = None,
                 text: List[str] | None = None) -> np.ndarray:
    """Vẽ overlay debug lên ảnh (dùng cho live view / MJPEG)."""
    out = bgr.copy()
    if not det.found:
        cv2.putText(out, "KHONG THAY COC", (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return out
    cv2.rectangle(out, (det.x0, det.y0), (det.x1, det.y1), (0, 200, 255), 1)
    cv2.line(out, (det.x0 - 8, int(det.rim_y_px)), (det.x1 + 8, int(det.rim_y_px)), (255, 200, 0), 1)
    cv2.line(out, (det.x0 - 8, int(det.base_y_px)), (det.x1 + 8, int(det.base_y_px)), (255, 200, 0), 1)
    if det.waterline_found:
        y = int(round(det.waterline_y_px))
        cv2.line(out, (det.x0 - 14, y), (det.x1 + 14, y), (0, 255, 0), 2)
    if target_mm is not None and det.mm_per_px > 0:
        yt = int(round(det.base_y_px - target_mm / det.mm_per_px))
        for xx in range(det.x0 - 14, det.x1 + 14, 10):
            cv2.line(out, (xx, yt), (min(xx + 5, det.x1 + 14), yt), (0, 100, 255), 2)
    lines = text or []
    for i, s in enumerate(lines):
        cv2.putText(out, s, (10, 24 + i * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out
