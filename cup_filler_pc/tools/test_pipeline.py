#!/usr/bin/env python3
"""Bộ tự kiểm chứng không cần phần cứng.

Chạy 3 bài:
  [1] DETECT  : 240 ảnh cốc trong suốt tổng hợp (nhiều cỡ cốc, mức nước, nhiễu,
                lệch camera) -> tỉ lệ tìm thấy cốc / vạch nước, sai số mm, ms/khung
  [2] FILL    : mô phỏng end-to-end bơm+camera+controller trên 9 tổ hợp
                (3 preset x 3 hình dạng cốc) -> sai số thể tích ml
  [3] PLOTS   : biểu đồ lỗi + đường cong rót, lưu vào report/

Cách chạy:  python3 tools/test_pipeline.py [--quick]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cupfiller.config import load_config                       # noqa: E402
from cupfiller.synthetic import make_test_set, SyntheticCupCamera, CupScene, CupGeometry  # noqa: E402
from cupfiller.detection import CupDetector                   # noqa: E402
from cupfiller.camera import Camera                           # noqa: E402
from cupfiller.pump import SimPump                            # noqa: E402
from cupfiller.controller import FillController, State        # noqa: E402

OUT = os.path.join(ROOT, "report")
os.makedirs(OUT, exist_ok=True)


def bench_detect(cfg, n_per_seed=80, seeds=(11, 22, 33)):
    det = CupDetector(cfg)
    rec = {"seeds": {}, "all": {}}
    errs_h, errs_v, found, wl, n_wl, fp, ms = [], [], 0, 0, 0, 0, []
    for seed in seeds:
        data = make_test_set(n_per_seed, seed=seed)
        e, f, w, nw, p, t = [], 0, 0, 0, 0, []
        for sc, img, gt in data:
            det.mm_per_px = 1.0 / sc.scale_px_per_mm
            t0 = time.time()
            r = det.detect(img)
            t.append(time.time() - t0)
            if r.found:
                f += 1
            if gt["height_mm"] > 2:
                nw += 1
            elif r.waterline_found:
                p += 1
            if r.waterline_found and gt["height_mm"] > 2:
                w += 1
                e.append(r.water_height_mm - gt["height_mm"])
                errs_h.append(e[-1])
                errs_v.append(r.volume_ml - gt["volume_ml"])
        rec["seeds"][str(seed)] = {
            "cup_found": f, "n": len(data), "wl_found": w, "n_wl": nw, "false_pos": p,
            "rms_mm": float(np.sqrt(np.mean(np.square(e)))) if e else None,
            "mean_ms": float(1000 * np.mean(t)),
        }
        found += f; wl += w; n_wl += nw; fp += p; ms += t
    rec["all"] = {
        "cup_found": found, "n": len(seeds) * n_per_seed,
        "wl_found": wl, "n_wl": n_wl, "false_pos": fp,
        "rms_mm": float(np.sqrt(np.mean(np.square(errs_h)))) if errs_h else None,
        "max_mm": float(np.max(np.abs(errs_h))) if errs_h else None,
        "mean_ms": float(1000 * np.mean(ms)),
        "vol_rms_ml": float(np.sqrt(np.mean(np.square(errs_v)))) if errs_v else None,
        "err_series": [round(x, 2) for x in errs_h],
    }
    return rec


def bench_fill(cfg, presets=(100, 200, 300)):
    geoms = [
        ("cup A 100mm", CupGeometry()),
        ("cup B 120mm hep", CupGeometry(h_mm=120, r_base_mm=26, r_rim_mm=40)),
        ("cup C 130mm loe", CupGeometry(h_mm=130, r_base_mm=33, r_rim_mm=42, y_base_mm=-60)),
    ]
    rows, hist_one = [], None
    for preset in presets:
        for gi, (name, geom) in enumerate(geoms):
            syn = SyntheticCupCamera(CupScene(cup=geom), seed=5 + gi, fps=15)
            cam = Camera("synthetic", cfg, scene=syn)
            pump = SimPump(cfg, syn)
            ctl = FillController(cfg, cam, pump)
            ctl.set_preset(preset)
            ctl.start()
            for _ in range(15 * 60):
                ctl.tick(1 / 15)
                if ctl.state in (State.DONE, State.FAULT):
                    break
            truth = syn.volume_ml
            rows.append({
                "preset_ml": preset, "cup": name, "state": ctl.state.value,
                "true_ml": round(truth, 1), "err_ml": round(truth - preset, 1),
                "err_pct": round(100 * (truth - preset) / preset, 1),
                "time_s": round(ctl.elapsed, 1), "alert": ctl.alert,
            })
            if hist_one is None and preset == 200 and gi == 0:
                hist_one = [list(x) for x in ctl.history]
    errs = [r["err_ml"] for r in rows]
    summary = {
        "rows": rows,
        "mean_ml": float(np.mean(errs)),
        "rms_ml": float(np.sqrt(np.mean(np.square(errs)))),
        "max_ml": float(np.max(np.abs(errs))),
        "n_done": sum(1 for r in rows if r["state"] == "DONE"),
    }
    return summary, hist_one


def make_plots(drec, frec, hist):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    files = []
    # 1) histogram lỗi vạch nước
    e = np.array(drec["all"]["err_series"])
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
    ax[0].hist(e, bins=30, color="#2a9d8f")
    ax[0].set_title("Sai số vạch nước (mm) - %d ảnh" % len(e))
    ax[0].set_xlabel("mm"); ax[0].grid(alpha=.3)
    ax[1].plot(e, ".", ms=3, color="#e76f51")
    ax[1].axhline(0, color="k", lw=.6)
    ax[1].set_title("Sai số theo thứ tự mẫu"); ax[1].set_xlabel("mẫu"); ax[1].grid(alpha=.3)
    fig.tight_layout(); p1 = os.path.join(OUT, "detect_errors.png"); fig.savefig(p1, dpi=130); files.append(p1); plt.close(fig)
    # 2) sai số thể tích từng ca rót
    rows = frec["rows"]
    labels = ["%dml\n%s" % (r["preset_ml"], r["cup"].split()[1]) for r in rows]
    vals = [r["err_ml"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.bar(range(len(vals)), vals, color=["#2a9d8f" if abs(v) <= 5 else "#e9c46a" for v in vals])
    ax.set_xticks(range(len(vals))); ax.set_xticklabels(labels, fontsize=7, rotation=90)
    ax.axhline(0, color="k", lw=.6); ax.set_ylabel("sai số (ml)")
    ax.set_title("Sai số thể tích sau rót (mean %+.1f ml, rms %.1f ml)" % (frec["mean_ml"], frec["rms_ml"]))
    ax.grid(alpha=.3, axis="y")
    fig.tight_layout(); p2 = os.path.join(OUT, "fill_errors.png"); fig.savefig(p2, dpi=130); files.append(p2); plt.close(fig)
    # 3) đường cong rót 200ml
    if hist:
        h = np.array(hist, dtype=object)
        t = np.array([x[0] for x in hist], float)
        hh = np.array([x[1] for x in hist], float)
        pw = np.array([x[2] for x in hist], float)
        fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8, 4.6))
        ax[0].plot(t, hh, color="#264653", label="mực nước đo (mm)")
        ax[0].axhline(hh[-1], color="#e76f51", ls="--", lw=.8, label="đích")
        ax[0].set_ylabel("mm"); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
        ax[1].fill_between(t, 0, pw, step="post", color="#2a9d8f", alpha=.7)
        ax[1].set_ylabel("PWM"); ax[1].set_xlabel("s"); ax[1].grid(alpha=.3)
        ax[0].set_title("Diễn biến một lần rót 200 ml (COARSE -> FINE -> SETTLING)")
        fig.tight_layout(); p3 = os.path.join(OUT, "fill_curve.png"); fig.savefig(p3, dpi=130); files.append(p3); plt.close(fig)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="bỏ seed thứ 3 và bớt ca rót")
    args = ap.parse_args()
    cfg = load_config(None)
    cfg.set("control.pump.type", "sim")
    cfg.set("control.loop.fps", 15)

    print("== [1/3] DETECT ==")
    seeds = (11, 22) if args.quick else (11, 22, 33)
    drec = bench_detect(cfg, n_per_seed=40 if args.quick else 80, seeds=seeds)
    for k, v in drec["seeds"].items():
        print("  seed %s: cốc %d/%d | vạch nước %d/%d | false+ %d | rms %.2f mm | %.1f ms/khung"
              % (k, v["cup_found"], v["n"], v["wl_found"], v["n_wl"], v["false_pos"], v["rms_mm"] or 0, v["mean_ms"]))
    a = drec["all"]
    print("  TONG: cốc %d/%d | vạch nước %d/%d | rms %.2f mm | max %.2f mm | %.1f ms/khung"
          % (a["cup_found"], a["n"], a["wl_found"], a["n_wl"], a["rms_mm"], a["max_mm"], a["mean_ms"]))

    print("== [2/3] FILL ==")
    presets = (200,) if args.quick else (100, 200, 300)
    frec, hist = bench_fill(cfg, presets=presets)
    for r in frec["rows"]:
        print("  %3d ml | %-14s | %-6s | sai %+.1f ml (%+.1f%%) | %.1fs %s"
              % (r["preset_ml"], r["cup"], r["state"], r["err_ml"], r["err_pct"], r["time_s"], r["alert"]))
    print("  TONG: mean %+.2f ml | rms %.2f ml | max %.2f ml | DONE %d/%d"
          % (frec["mean_ml"], frec["rms_ml"], frec["max_ml"], frec["n_done"], len(frec["rows"])))

    print("== [3/3] PLOTS ==")
    files = make_plots(drec, frec, hist)
    for f in files:
        print("  ", os.path.relpath(f, ROOT))
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as fh:
        json.dump({"detect": drec, "fill": frec}, fh, ensure_ascii=False, indent=1)
    print("  report/results.json")


if __name__ == "__main__":
    main()
