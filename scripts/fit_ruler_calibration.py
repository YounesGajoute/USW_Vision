#!/usr/bin/env python3
"""
Fit pixel → mm scale from ruler calibration captures.

Reads all cal_*.png in a session folder (under backend/storage/Calibration/),
detects yellow metric tape ticks, and writes calibration.json.

Usage:
  python3 scripts/fit_ruler_calibration.py
  python3 scripts/fit_ruler_calibration.py backend/storage/Calibration/session_20260517_081705
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_CALIB_ROOT = _REPO_ROOT / "backend" / "storage" / "Calibration"


def find_yellow_bbox(img: np.ndarray) -> Optional[Dict[str, Any]]:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([12, 70, 70]), np.array([48, 255, 255]))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area > best_area and area > 8000:
            best_area = area
            best = (x, y, w, h)
    if not best:
        return None
    x, y, w, h = best
    return {
        "x": int(x),
        "y": int(y),
        "w": int(w),
        "h": int(h),
        "vertical": h > w * 1.1,
        "horizontal": w > h * 1.1,
    }


def edge_tick_positions(
    img: np.ndarray, bbox: Dict[str, Any]
) -> Tuple[List[float], float, bool]:
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    vertical = bbox["vertical"]
    if vertical:
        cx = x + w // 2
        x0, x1 = max(0, cx - 18), min(img.shape[1], cx + 18)
        band = img[:, x0:x1]
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        proj = np.abs(np.diff(gray.astype(np.int16), axis=0)).max(axis=1).astype(np.float64)
        offset = y
    else:
        cy = y + h // 2
        y0, y1 = max(0, cy - 18), min(img.shape[0], cy + 18)
        band = img[y0:y1, :]
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        proj = np.abs(np.diff(gray.astype(np.int16), axis=1)).max(axis=0).astype(np.float64)
        offset = x

    proj = np.convolve(proj, np.ones(11) / 11, mode="same")
    peaks: List[int] = []
    thr = float(np.percentile(proj, 91))
    for i in range(4, len(proj) - 4):
        if proj[i] >= thr and proj[i] >= proj[i - 1] and proj[i] >= proj[i + 1]:
            if not peaks or i - peaks[-1] >= 26:
                peaks.append(i)
            elif proj[i] > proj[peaks[-1]]:
                peaks[-1] = i

    if len(peaks) < 3:
        return [], 0.0, vertical

    diffs = np.diff(peaks)
    valid = diffs[(diffs > 30) & (diffs < 75)]
    if len(valid) < 1:
        return [], 0.0, vertical

    step = float(np.median(valid))
    good = [peaks[0]]
    for p in peaks[1:]:
        d = p - good[-1]
        n = max(1, round(d / step))
        if abs(d - n * step) / step < 0.22:
            good.append(p)

    abs_px = [float(p + offset) for p in good]
    return abs_px, step / 10.0, vertical


def find_red_anchor(img: np.ndarray, bbox: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    sub = img[y : y + h, x : x + w]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    red = cv2.bitwise_or(
        cv2.inRange(hsv, np.array([0, 90, 70]), np.array([10, 255, 255])),
        cv2.inRange(hsv, np.array([165, 90, 70]), np.array([180, 255, 255])),
    )
    cnts, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs: List[Tuple[int, int, int]] = []
    for c in cnts:
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw * bh < 90:
            continue
        blobs.append((bx + bw // 2 + x, by + bh // 2 + y, bw * bh))
    blobs.sort(key=lambda b: -b[2])
    return (blobs[0][0], blobs[0][1]) if blobs else None


def solve_anchor(
    ticks_px: List[float],
    px_per_mm: float,
    anchor_coord: float,
    candidates: Tuple[int, ...] = (20, 10, 30),
) -> Optional[Dict[str, Any]]:
    best = None
    for acm in candidates:
        anchor_mm = acm * 10.0
        intercept = anchor_coord - anchor_mm * px_per_mm
        residuals = [((px - intercept) / px_per_mm) / 10.0 - round(((px - intercept) / px_per_mm) / 10.0) for px in ticks_px]
        rms = float(np.sqrt(np.mean(np.array(residuals) ** 2)))
        if best is None or rms < best["rms_cm"]:
            best = {
                "anchor_cm": acm,
                "intercept_px": float(intercept),
                "rms_cm": rms,
            }
    if best and best["rms_cm"] < 0.25:
        mm0 = (ticks_px[0] - best["intercept_px"]) / px_per_mm
        mm1 = (ticks_px[-1] - best["intercept_px"]) / px_per_mm
        best["cm_range"] = [mm0 / 10.0, mm1 / 10.0]
        return best
    return None


def aggregate(values: List[float]) -> Dict[str, float]:
    arr = np.array(values, dtype=float)
    return {
        "count": int(len(arr)),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def fit_session(session_dir: Path) -> Dict[str, Any]:
    images = sorted(session_dir.glob("cal_*.png"))
    if not images:
        raise SystemExit(f"No cal_*.png in {session_dir}")

    per_image: List[Dict[str, Any]] = []
    for path in images:
        img = cv2.imread(str(path))
        if img is None:
            continue
        h, w = img.shape[:2]
        bbox = find_yellow_bbox(img)
        if not bbox:
            per_image.append({"file": path.name, "error": "ruler_not_found"})
            continue

        ticks, px_per_mm, vertical = edge_tick_positions(img, bbox)
        axis = "y" if vertical else "x"
        entry: Dict[str, Any] = {
            "file": path.name,
            "image_size": [w, h],
            "ruler_bbox": bbox,
            "measurement_axis": axis,
            "cm_tick_positions_px": ticks,
            "n_ticks": len(ticks),
            "px_per_mm": round(px_per_mm, 4),
            "px_per_cm": round(px_per_mm * 10, 3),
            "mm_per_px": round(1.0 / px_per_mm, 4) if px_per_mm else None,
        }
        red = find_red_anchor(img, bbox)
        if red and len(ticks) >= 3 and px_per_mm > 0:
            coord = float(red[1] if vertical else red[0])
            anchor = solve_anchor(ticks, px_per_mm, coord)
            if anchor:
                entry["red_digit_anchor"] = {
                    "pixel": {"x": int(red[0]), "y": int(red[1])},
                    **anchor,
                }
        per_image.append(entry)

    good = [e for e in per_image if e.get("n_ticks", 0) >= 4 and e.get("px_per_mm")]
    vert = [e for e in good if e["measurement_axis"] == "y"]
    horiz = [e for e in good if e["measurement_axis"] == "x"]

    med = float(np.median([e["px_per_mm"] for e in good])) if good else 0.0
    result: Dict[str, Any] = {
        "session_id": session_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "yellow_ruler_edge_tick_detection",
        "unit": "mm",
        "per_image": per_image,
        "aggregate": {
            "all_images_px_per_mm": aggregate([e["px_per_mm"] for e in good]) if good else {},
            "vertical_ruler_px_per_mm": aggregate([e["px_per_mm"] for e in vert]) if vert else {},
            "horizontal_ruler_px_per_mm": aggregate([e["px_per_mm"] for e in horiz]) if horiz else {},
        },
        "recommended": {
            "px_per_mm": round(med, 4),
            "px_per_cm": round(med * 10, 3),
            "mm_per_px": round(1.0 / med, 4) if med else None,
            "formula_mm": "mm = (position_px - intercept_px) / px_per_mm",
            "formula_px": "position_px = intercept_px + mm * px_per_mm",
        },
    }

    vert_anchors = [e for e in vert if e.get("red_digit_anchor")]
    horiz_anchors = [e for e in horiz if e.get("red_digit_anchor")]
    if vert_anchors:
        result["recommended"]["intercept_px_y"] = round(
            float(np.median([e["red_digit_anchor"]["intercept_px"] for e in vert_anchors])), 2
        )
    if horiz_anchors:
        result["recommended"]["intercept_px_x"] = round(
            float(np.median([e["red_digit_anchor"]["intercept_px"] for e in horiz_anchors])), 2
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit ruler px→mm from a calibration session folder")
    parser.add_argument(
        "session_dir",
        nargs="?",
        type=Path,
        help="Session folder (default: latest under storage/Calibration)",
    )
    args = parser.parse_args()

    if args.session_dir:
        session = Path(args.session_dir).expanduser().resolve()
    else:
        sessions = sorted(_DEFAULT_CALIB_ROOT.glob("session_*"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            raise SystemExit(f"No sessions under {_DEFAULT_CALIB_ROOT}")
        session = sessions[-1]

    cal = fit_session(session)
    out = session / "calibration.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=2)
        f.write("\n")

    rec = cal["recommended"]
    print(f"Session: {session.name}")
    print(f"Wrote:   {out}")
    print(f"Scale:   {rec['px_per_mm']} px/mm  ({rec['mm_per_px']} mm/px)")
    agg = cal["aggregate"].get("all_images_px_per_mm", {})
    if agg:
        print(f"Spread:  {agg['min']:.4f} – {agg['max']:.4f} px/mm (σ={agg['std']:.4f}, n={agg['count']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
