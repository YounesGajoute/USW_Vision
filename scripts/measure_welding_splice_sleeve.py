#!/usr/bin/env python3
"""
Measure welding splice and heat-shrink sleeve on inspection images.

Supports full assembly (welding splice + sleeve), welding-splice-only, and sleeve-only captures.
Welding-splice-only images use expanded copper segmentation and axis-profile refinement.

Definitions (top-down camera):
  Length (L) — extent along the component's main axis (wire direction / tube axis).
  Height (H) — cross-section perpendicular to that axis (welding splice thickness, sleeve OD).

Scale: px/mm from a calibration session (calibration.json) or --px-per-mm.

Examples:
  python3 scripts/measure_welding_splice_sleeve.py image.png
  python3 scripts/measure_welding_splice_sleeve.py image.png --annotate-out /tmp/out.png
  python3 scripts/measure_welding_splice_sleeve.py image.png --calibration backend/storage/Calibration/session_20260517_081705
  python3 scripts/measure_welding_splice_sleeve.py backend/storage/image_history/12/*.png --json-out results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_CALIB = _REPO_ROOT / "backend" / "storage" / "Calibration"
_SLEEVE_REF_DIR = _REPO_ROOT / "backend" / "storage" / "reference" / "sleeve"
_SLEEVE_REF_JSON = _SLEEVE_REF_DIR / "sleeve_reference.json"
_SLEEVE_REF_CACHE: Optional[Dict[str, Any]] = None

# BGR drawing colors
_COLOR_WELDING_SPLICE = (0, 140, 255)
_COLOR_SLEEVE = (255, 0, 255)
_COLOR_LENGTH = (0, 140, 255)
_COLOR_HEIGHT = (0, 255, 255)


@dataclass
class AxisMeasurement:
    """Oriented size from contour PCA."""

    length_px: float
    height_px: float
    axis_angle_deg: float
    centroid: Tuple[float, float]
    major_unit: Tuple[float, float]
    minor_unit: Tuple[float, float]

    @property
    def length_mm(self) -> float:
        return self._mm(self.length_px)

    @property
    def height_mm(self) -> float:
        return self._mm(self.height_px)

    _px_per_mm: float = 5.1

    def _mm(self, px: float) -> float:
        return px / self._px_per_mm if self._px_per_mm > 0 else 0.0

    def to_mm_dict(self) -> Dict[str, float]:
        return {
            "length_mm": round(self.length_mm, 2),
            "height_mm": round(self.height_mm, 2),
            "length_px": round(self.length_px, 1),
            "height_px": round(self.height_px, 1),
            "axis_angle_deg": round(self.axis_angle_deg, 1),
        }


@dataclass
class ComponentResult:
    name: str
    found: bool
    measurement: Optional[AxisMeasurement] = None
    contour: Optional[np.ndarray] = None
    aabb: Optional[Tuple[int, int, int, int]] = None
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "found": self.found,
            "debug": self.debug,
        }
        if self.aabb:
            x, y, w, h = self.aabb
            d["aabb"] = {"x": x, "y": y, "w": w, "h": h}
        if self.measurement:
            d.update(self.measurement.to_mm_dict())
            d["definitions"] = {
                "length": "along component main axis",
                "height": "cross-section perpendicular to axis (OD / thickness)",
            }
        return d


@dataclass
class ImageMeasurementResult:
    image_path: str
    image_size: Tuple[int, int]
    px_per_mm: float
    px_per_mm_source: str
    cable_band_y: Optional[Tuple[int, int]] = None
    welding_splice: Optional[ComponentResult] = None
    sleeve: Optional[ComponentResult] = None
    errors: List[str] = field(default_factory=list)
    # uint8 masks (0/255) aligned with measurement contours
    welding_splice_mask: Optional[np.ndarray] = field(default=None, repr=False)
    sleeve_mask: Optional[np.ndarray] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "image_path": self.image_path,
            "image_size": {"width": self.image_size[0], "height": self.image_size[1]},
            "px_per_mm": self.px_per_mm,
            "px_per_mm_source": self.px_per_mm_source,
            "cable_band_y": (
                {"y0": self.cable_band_y[0], "y1": self.cable_band_y[1]}
                if self.cable_band_y
                else None
            ),
            "welding_splice": self.welding_splice.to_dict() if self.welding_splice else None,
            "heat_shrink_sleeve": self.sleeve.to_dict() if self.sleeve else None,
            "errors": self.errors,
        }


def load_px_per_mm(
    calibration_dir: Optional[Path],
    cli_px_per_mm: Optional[float],
) -> Tuple[float, str]:
    if cli_px_per_mm is not None and cli_px_per_mm > 0:
        return cli_px_per_mm, "--px-per-mm"

    if calibration_dir is not None:
        cal_path = calibration_dir / "calibration.json"
        if cal_path.is_file():
            with open(cal_path, encoding="utf-8") as f:
                cal = json.load(f)
            rec = cal.get("recommended") or {}
            px = rec.get("px_per_mm")
            if px and float(px) > 0:
                return float(px), str(cal_path)

    # latest session
    sessions = sorted(_DEFAULT_CALIB.glob("session_*"), key=lambda p: p.stat().st_mtime)
    for session in reversed(sessions):
        cal_path = session / "calibration.json"
        if cal_path.is_file():
            with open(cal_path, encoding="utf-8") as f:
                cal = json.load(f)
            px = (cal.get("recommended") or {}).get("px_per_mm")
            if px and float(px) > 0:
                return float(px), str(cal_path)

    return 5.1, "default_fallback"


def _largest_contour(mask: np.ndarray, min_area: float) -> Optional[np.ndarray]:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = min_area
    for c in cnts:
        a = cv2.contourArea(c)
        if a >= best_area:
            best_area = a
            best = c
    return best


def _pca_measurement(contour: np.ndarray, px_per_mm: float) -> AxisMeasurement:
    pts = contour.reshape(-1, 2).astype(np.float64)
    mean = pts.mean(axis=0)
    centered = pts - mean
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    major = eigvecs[:, order[0]]
    minor = eigvecs[:, order[1]]
    along = centered @ major
    perp = centered @ minor
    length_px = float(np.ptp(along))
    height_px = float(np.ptp(perp))
    angle = float(np.degrees(np.arctan2(major[1], major[0])))
    m = AxisMeasurement(
        length_px=length_px,
        height_px=height_px,
        axis_angle_deg=angle,
        centroid=(float(mean[0]), float(mean[1])),
        major_unit=(float(major[0]), float(major[1])),
        minor_unit=(float(minor[0]), float(minor[1])),
    )
    m._px_per_mm = px_per_mm
    return m


def _pca_measurement_welding_splice(
    contour: np.ndarray,
    img: np.ndarray,
    px_per_mm: float,
) -> AxisMeasurement:
    """
    Welding splice L/H from robust percentiles on barrel-metal pixels only.
    Full-contour PCA inflates H when wire jackets share the mask edge.
    """
    pts = contour.reshape(-1, 2).astype(np.float64)
    h, w = img.shape[:2]
    barrel = _welding_splice_barrel_mask(img)
    metal_pts: List[np.ndarray] = []
    for p in pts:
        x, y = int(round(p[0])), int(round(p[1]))
        if 0 <= x < w and 0 <= y < h and barrel[y, x] > 0:
            metal_pts.append(p)
    if len(metal_pts) >= 50:
        pts = np.stack(metal_pts, axis=0)

    mean = pts.mean(axis=0)
    centered = pts - mean
    if centered.shape[0] < 8:
        return _pca_measurement(contour, px_per_mm)

    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    major = eigvecs[:, order[0]]
    minor = eigvecs[:, order[1]]
    if major[0] < 0:
        major = -major
        minor = -minor
    along = centered @ major
    perp = centered @ minor
    length_px = float(np.percentile(along, 93) - np.percentile(along, 7))
    height_px = float(np.percentile(perp, 86) - np.percentile(perp, 14))
    if length_px < 8:
        length_px = float(np.ptp(along))
    if height_px < 4:
        height_px = float(np.ptp(perp))
    height_px = min(height_px, length_px * 0.58)
    angle = float(np.degrees(np.arctan2(major[1], major[0])))
    m = AxisMeasurement(
        length_px=length_px,
        height_px=height_px,
        axis_angle_deg=angle,
        centroid=(float(mean[0]), float(mean[1])),
        major_unit=(float(major[0]), float(major[1])),
        minor_unit=(float(minor[0]), float(minor[1])),
    )
    m._px_per_mm = px_per_mm
    return m


def detect_cable_band(img: np.ndarray) -> Tuple[int, int]:
    """
    Estimate vertical extent of the main cable assembly.
    Excludes the top reference wire band (red wire).
    Improved: uses adaptive percentile and explicit red-wire exclusion.
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    red1 = cv2.inRange(hsv,
                       np.array([0, 100, 60], dtype=np.uint8),
                       np.array([8, 255, 255], dtype=np.uint8))
    red2 = cv2.inRange(hsv,
                       np.array([168, 100, 60], dtype=np.uint8),
                       np.array([180, 255, 255], dtype=np.uint8))
    red_wire = cv2.bitwise_or(red1, red2)
    red_rows = np.where(red_wire.sum(axis=1) > w * 0.05)[0]
    red_bottom = int(red_rows.max()) + 15 if len(red_rows) > 0 else int(h * 0.12)
    red_bottom = min(red_bottom, int(h * 0.25))

    blue = cv2.inRange(hsv,
                       np.array([85, 40, 30], dtype=np.uint8),
                       np.array([135, 255, 255], dtype=np.uint8))
    copper = cv2.inRange(hsv,
                         np.array([5, 20, 50], dtype=np.uint8),
                         np.array([32, 255, 255], dtype=np.uint8))
    dark = (gray < 115).astype(np.uint8) * 255
    green_yellow = cv2.inRange(hsv,
                               np.array([20, 60, 60], dtype=np.uint8),
                               np.array([85, 255, 255], dtype=np.uint8))
    activity = cv2.bitwise_or(blue, cv2.bitwise_or(
        copper, cv2.bitwise_or(dark, green_yellow)))

    row_sum = activity.sum(axis=1).astype(np.float64)
    row_sum = np.convolve(row_sum, np.ones(21) / 21, mode="same")
    thresh = max(np.percentile(row_sum, 65), row_sum.max() * 0.10)
    rows = np.where(row_sum > thresh)[0]
    if len(rows) < 10:
        return int(h * 0.40), int(h * 0.80)

    rows = rows[rows > red_bottom]
    if len(rows) < 5:
        return int(h * 0.40), int(h * 0.80)

    y0 = int(max(0, rows.min() - 30))
    y1 = int(min(h, rows.max() + 30))
    return y0, y1


def _is_likely_sleeve_only_scene(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
) -> bool:
    """Matte black tube dominates and copper is sparse → sleeve-only capture."""
    h, w = img.shape[:2]
    y_m0 = max(0, cable_y0 - 35)
    y_m1 = min(h, cable_y1 + 35)
    band = np.zeros((h, w), np.uint8)
    band[y_m0:y_m1, :] = 255
    tube_px = int(cv2.countNonZero(cv2.bitwise_and(_tube_mask_standalone(img), band)))
    copper_px = int(cv2.countNonZero(cv2.bitwise_and(_copper_mask_narrow(img), band)))
    if tube_px > 14000 and copper_px < 1200:
        return True
    ref = load_sleeve_reference()
    if ref is not None:
        matte_px = int(
            cv2.countNonZero(cv2.bitwise_and(_matte_tube_mask_reference(img, ref), band))
        )
        if matte_px > 18000 and copper_px < 2000:
            return True
    return False


def _refined_welding_splice_barrel_ok(contour: np.ndarray) -> bool:
    """Compact copper barrel shape independent of wide-band seed size."""
    _, _, rbw, rbh = cv2.boundingRect(contour)
    if rbw < 38 or rbw > 120 or rbh < 10 or rbh > 66:
        return False
    elong = max(rbw, rbh) / max(1, min(rbw, rbh))
    if elong < 1.0 or elong > 10.0:
        return False
    return min(rbw, rbh) / max(rbw, rbh) >= 0.62


def _refined_welding_splice_geometry_ok(
    seed_contour: np.ndarray,
    refined_contour: np.ndarray,
) -> bool:
    """Reject refine results that collapsed to a vertical sliver or shrank too much."""
    if _refined_welding_splice_barrel_ok(refined_contour):
        sx, sy, sbw, sbh = cv2.boundingRect(seed_contour)
        if sbw > 200:
            return True
    sx, sy, sbw, sbh = cv2.boundingRect(seed_contour)
    rx, ry, rbw, rbh = cv2.boundingRect(refined_contour)
    seed_area = cv2.contourArea(seed_contour)
    ref_area = cv2.contourArea(refined_contour)
    if ref_area < seed_area * 0.35:
        return False
    if rbw < sbw * 0.45:
        return False
    elong = max(rbw, rbh) / max(1, min(rbw, rbh))
    if elong < 1.15:
        return False
    if rbw < rbh * 0.85:
        return False
    return True


def _copper_fraction_in_contour(img: np.ndarray, contour: np.ndarray) -> float:
    """Share of contour interior that matches copper mask (wide + orange)."""
    h, w = img.shape[:2]
    region = np.zeros((h, w), np.uint8)
    cv2.drawContours(region, [contour], -1, 255, -1)
    copper = _copper_mask(img)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(
        hsv,
        np.array([95, 85, 40], dtype=np.uint8),
        np.array([125, 255, 255], dtype=np.uint8),
    )
    copper = cv2.bitwise_or(copper, orange)
    inside = region > 0
    if inside.sum() < 30:
        return 0.0
    return float(cv2.countNonZero(cv2.bitwise_and(copper, region))) / float(inside.sum())


def _matte_sleeve_fraction(img: np.ndarray, contour: np.ndarray) -> float:
    """Share of contour interior that matches reference matte tube colors."""
    ref = load_sleeve_reference()
    if ref is None:
        return 0.0
    h, w = img.shape[:2]
    region = np.zeros((h, w), np.uint8)
    cv2.drawContours(region, [contour], -1, 255, -1)
    tube = _matte_tube_mask_reference(img, ref)
    inside = region > 0
    if inside.sum() < 30:
        return 0.0
    return float(cv2.countNonZero(cv2.bitwise_and(tube, region))) / float(inside.sum())


def _is_wire_insulation_false_copper(
    sat_med: float,
    hue_med: float,
    val_med: float,
    copper_frac: float,
) -> bool:
    """
    Insulated strand hues that match narrow copper HSV but are not metal.
    Dull barrel copper: low saturation (S often < 80); wires: S > 95.
    """
    if 36 <= hue_med <= 92:
        return True
    if sat_med > 95 and 16 <= hue_med <= 34 and val_med > 110 and copper_frac < 0.55:
        return True
    if sat_med > 120 and 95 <= hue_med <= 125:
        return True
    return False


def _welding_splice_color_valid(img: np.ndarray, contour: np.ndarray) -> bool:
    """Reject matte-black sleeves and wire insulation misclassified as copper."""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    vals = hsv[mask > 0]
    if vals.shape[0] < 40:
        return True
    sat_med = float(np.median(vals[:, 1]))
    hue_med = float(np.median(vals[:, 0]))
    val_med = float(np.median(vals[:, 2]))
    if sat_med < 22 and val_med < 95:
        return False
    if _matte_sleeve_fraction(img, contour) > 0.42:
        return False
    copper_frac = _copper_fraction_in_contour(img, contour)
    if copper_frac < 0.18:
        return False
    if _is_wire_insulation_false_copper(sat_med, hue_med, val_med, copper_frac):
        return False
    # orange/copper barrel (incl. H≈112 on some captures)
    if (2 <= hue_med <= 35) or (95 <= hue_med <= 125 and sat_med >= 70):
        return True
    return sat_med >= 55 and val_med >= 45


def _copper_mask_narrow(img: np.ndarray) -> np.ndarray:
    """Tight copper HSV for compact welding splice seed (avoids merging long wire strands)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    copper = cv2.inRange(hsv, np.array([5, 70, 55]), np.array([24, 255, 255]))
    orange = cv2.inRange(
        hsv,
        np.array([95, 85, 40], dtype=np.uint8),
        np.array([125, 255, 255], dtype=np.uint8),
    )
    copper = cv2.bitwise_or(copper, orange)
    copper = cv2.morphologyEx(
        copper,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        2,
    )
    copper = cv2.morphologyEx(
        copper,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        1,
    )
    return copper


def _copper_mask(img: np.ndarray) -> np.ndarray:
    """
    Copper/brass welding splice: covers dull, mid-tone, and specular highlights.
    Measured HSV on real captures: H=13–27, S=29–99, V=170–241.
    Excludes green/yellow wire insulation and blue wires.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    _, g, r = cv2.split(img)

    dull = cv2.inRange(hsv,
                       np.array([5, 20, 50], dtype=np.uint8),
                       np.array([28, 255, 255], dtype=np.uint8))

    mid = cv2.inRange(hsv,
                      np.array([3, 10, 120], dtype=np.uint8),
                      np.array([32, 255, 255], dtype=np.uint8))

    highlight = cv2.inRange(hsv,
                            np.array([0, 5, 180], dtype=np.uint8),
                            np.array([35, 70, 255], dtype=np.uint8))
    highlight = cv2.bitwise_and(
        highlight,
        cv2.inRange(cv2.subtract(r, g),
                    np.array([8], dtype=np.uint8),
                    np.array([255], dtype=np.uint8))
    )

    not_wire = cv2.bitwise_not(
        cv2.inRange(hsv,
                    np.array([22, 80, 80], dtype=np.uint8),
                    np.array([95, 255, 255], dtype=np.uint8))
    )
    not_blue = cv2.bitwise_not(
        cv2.inRange(hsv,
                    np.array([85, 40, 40], dtype=np.uint8),
                    np.array([135, 255, 255], dtype=np.uint8))
    )
    not_red = cv2.bitwise_not(
        cv2.inRange(hsv,
                    np.array([0, 80, 60], dtype=np.uint8),
                    np.array([5, 255, 255], dtype=np.uint8))
    )
    not_red2 = cv2.bitwise_not(
        cv2.inRange(hsv,
                    np.array([165, 80, 60], dtype=np.uint8),
                    np.array([180, 255, 255], dtype=np.uint8))
    )
    not_colored = cv2.bitwise_and(cv2.bitwise_and(not_wire, not_blue),
                                   cv2.bitwise_and(not_red, not_red2))

    copper = cv2.bitwise_and(
        cv2.bitwise_or(dull, cv2.bitwise_or(mid, highlight)),
        not_colored,
    )
    copper = cv2.morphologyEx(
        copper, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=2
    )
    copper = cv2.morphologyEx(
        copper, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1
    )
    return copper


def _is_insulation_hsv(h: int, s: int, v: int) -> bool:
    """Colored wire jacket hues that bleed into the wide copper mask."""
    if 36 <= h <= 92:
        return True
    if 75 <= h <= 110 and s >= 45:
        return True
    if s >= 100 and 14 <= h <= 32 and v >= 100:
        return True
    return False


def _welding_splice_barrel_mask(img: np.ndarray) -> np.ndarray:
    """
    Tight mask for the welding splice barrel only: dull/specular copper and orange metal.
    Excludes blue/green/yellow insulation that _copper_mask can include.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(img)
    rg = cv2.compare(cv2.subtract(r, g), np.array(6, dtype=np.uint8), cv2.CMP_GT)

    dull = cv2.inRange(
        hsv,
        np.array([4, 18, 55], dtype=np.uint8),
        np.array([28, 255, 255], dtype=np.uint8),
    )
    bright = cv2.inRange(
        hsv,
        np.array([0, 5, 175], dtype=np.uint8),
        np.array([35, 65, 255], dtype=np.uint8),
    )
    orange = cv2.inRange(
        hsv,
        np.array([95, 70, 45], dtype=np.uint8),
        np.array([125, 255, 255], dtype=np.uint8),
    )
    metal = cv2.bitwise_or(dull, cv2.bitwise_or(bright, orange))
    metal = cv2.bitwise_and(metal, rg)

    wire = cv2.inRange(
        hsv,
        np.array([28, 55, 55], dtype=np.uint8),
        np.array([95, 255, 255], dtype=np.uint8),
    )
    blue = cv2.inRange(
        hsv,
        np.array([72, 40, 45], dtype=np.uint8),
        np.array([112, 255, 255], dtype=np.uint8),
    )
    sat_brown = cv2.inRange(
        hsv,
        np.array([12, 98, 90], dtype=np.uint8),
        np.array([34, 255, 255], dtype=np.uint8),
    )
    exclude = cv2.bitwise_or(wire, cv2.bitwise_or(blue, sat_brown))
    out = cv2.bitwise_and(metal, cv2.bitwise_not(exclude))
    out = cv2.morphologyEx(
        out,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        1,
    )
    out = cv2.morphologyEx(
        out,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        1,
    )
    return out


def _strip_insulation_from_welding_splice(
    img: np.ndarray,
    contour: np.ndarray,
) -> np.ndarray:
    """Zero wire-jacket pixels inside a welding splice mask and return the largest contour."""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    ys, xs = np.where(mask > 0)
    for y, x in zip(ys, xs):
        hv = hsv[y, x]
        if _is_insulation_hsv(int(hv[0]), int(hv[1]), int(hv[2])):
            mask[y, x] = 0
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        1,
    )
    trimmed = _largest_contour(mask, 220)
    return trimmed if trimmed is not None else contour


def _tighten_welding_splice_contour(
    img: np.ndarray,
    seed_contour: np.ndarray,
    barrel_mask: np.ndarray,
) -> Optional[np.ndarray]:
    """Pick the compact barrel blob nearest the seed inside a padded ROI."""
    h, w = img.shape[:2]
    sx, sy, sbw, sbh = cv2.boundingRect(seed_contour)
    seed_cx = sx + sbw / 2
    seed_cy = sy + sbh / 2
    pad_x, pad_y = 18, 14
    x0 = max(0, sx - pad_x)
    y0 = max(0, sy - pad_y)
    x1 = min(w, sx + sbw + pad_x)
    y1 = min(h, sy + sbh + pad_y)
    roi = barrel_mask[y0:y1, x0:x1].copy()
    if roi.size == 0:
        return None
    cnts, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: Optional[np.ndarray] = None
    best_key = -1.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 220:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw < 34 or bw > 120 or bh < 10 or bh > 52:
            continue
        if bw < bh * 0.75:
            continue
        cx = bx + bw / 2 + x0
        cy = by + bh / 2 + y0
        dist = ((cx - seed_cx) ** 2 + (cy - seed_cy) ** 2) ** 0.5
        if dist > max(sbw, sbh) + 55:
            continue
        compact = area / max(bw * bh, 1)
        key = area * compact / (1.0 + dist * 0.04)
        if key > best_key:
            best_key = key
            c_full = c.copy()
            c_full[:, 0, 0] += x0
            c_full[:, 0, 1] += y0
            best = c_full
    return best


def _core_welding_splice_from_narrow_mask(
    img: np.ndarray,
    seed_contour: np.ndarray,
    cable_y0: int,
    cable_y1: int,
) -> Optional[np.ndarray]:
    """Compact welding splice blob from narrow copper inside the seed (drops wire bleed)."""
    h, w = img.shape[:2]
    sx, sy, sbw, sbh = cv2.boundingRect(seed_contour)
    pad_x, pad_y = 10, 8
    x0 = max(0, sx - pad_x)
    y0 = max(0, sy - pad_y)
    x1 = min(w, sx + sbw + pad_x)
    y1 = min(h, sy + sbh + pad_y)
    narrow = _copper_mask_narrow(img[y0:y1, x0:x1])
    barrel = _welding_splice_barrel_mask(img[y0:y1, x0:x1])
    metal = cv2.bitwise_or(narrow, barrel)
    cnts, _ = cv2.findContours(metal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    seed_cx = sx + sbw / 2
    seed_cy = sy + sbh / 2
    best: Optional[np.ndarray] = None
    best_key = -1.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 180:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw < 32 or bw > 95 or bh < 10 or bh > 50:
            continue
        cx = bx + bw / 2 + x0
        cy = by + bh / 2 + y0
        if abs(cx - seed_cx) > sbw * 0.55 + 20:
            continue
        if abs(cy - seed_cy) > sbh * 0.65 + 18:
            continue
        compact = area / max(bw * bh, 1)
        key = area * compact
        if key > best_key:
            best_key = key
            c_full = c.copy()
            c_full[:, 0, 0] += x0
            c_full[:, 0, 1] += y0
            best = c_full
    return best


def _finalize_welding_splice_contour(
    img: np.ndarray,
    contour: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    band_mask: np.ndarray,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Tighten seed, axis-profile refine on barrel metal, strip wire bleed."""
    debug: Dict[str, Any] = {}
    sx, sy, sbw, sbh = cv2.boundingRect(contour)
    seed_area = sbw * sbh

    core = _core_welding_splice_from_narrow_mask(img, contour, cable_y0, cable_y1)
    if core is not None:
        cx, cy, cw, ch = cv2.boundingRect(core)
        if cw * ch < seed_area * 0.92:
            contour = core
            debug["welding_splice_core_narrow"] = True

    seed_for_refine = contour
    barrel = cv2.bitwise_and(_welding_splice_barrel_mask(img), band_mask)
    refined_mask, refined_contour, refine_dbg = _refine_full_welding_splice_mask(
        img, contour, cable_y0, cable_y1, copper_mask=barrel
    )
    debug.update(refine_dbg)
    if refined_contour is not None:
        ok_geom = _refined_welding_splice_geometry_ok(seed_for_refine, refined_contour)
        ok_barrel = _refined_welding_splice_barrel_ok(refined_contour)
        rx, ry, rw, rh = cv2.boundingRect(refined_contour)
        smaller = rw * rh < cv2.boundingRect(seed_for_refine)[2] * cv2.boundingRect(seed_for_refine)[3]
        if (ok_geom or ok_barrel) and smaller and _welding_splice_color_valid(img, refined_contour):
            if cv2.contourArea(refined_contour) >= cv2.contourArea(seed_for_refine) * 0.22:
                contour = refined_contour
                debug["welding_splice_refined"] = True

    stripped = _strip_insulation_from_welding_splice(img, contour)
    if stripped is not None and cv2.contourArea(stripped) >= 200:
        contour = stripped
        debug["welding_splice_insulation_stripped"] = True

    return contour, debug


def _refine_full_welding_splice_mask(
    img: np.ndarray,
    init_contour: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    copper_mask: Optional[np.ndarray] = None,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Dict[str, Any]]:
    """
    Rebuild a solid welding splice mask by marching along the barrel axis on a copper mask.
    Trims thin wire strands at the ends; caps width to exclude cast shadow below.
    """
    h, w = img.shape[:2]
    debug: Dict[str, Any] = {"refined_full_welding_splice": True}
    mean, major, perp, s_min, s_max = _contour_axis_frame(init_contour)
    copper = copper_mask if copper_mask is not None else _copper_mask(img)

    seed_span = max(s_max - s_min, 20.0)
    pad = min(28.0, seed_span * 0.45)
    stations: List[Tuple[float, float, float, float]] = []
    step = 2.0
    s = s_min - pad
    while s <= s_max + pad:
        cx = int(round(mean[0] + major[0] * s))
        cy = int(round(mean[1] + major[1] * s))
        ts_hit: List[float] = []
        for t in np.linspace(-48, 48, 97):
            px = int(round(cx + perp[0] * t))
            py = int(round(cy + perp[1] * t))
            if 0 <= px < w and 0 <= py < h and copper[py, px] > 0:
                ts_hit.append(t)
        if len(ts_hit) >= 4:
            t0, t1 = float(min(ts_hit)), float(max(ts_hit))
            stations.append((s, t0, t1, t1 - t0))
        s += step

    if len(stations) < 8:
        return None, init_contour, {**debug, "refine_skipped": "too_few_stations"}

    widths = np.array([st[3] for st in stations], dtype=np.float64)
    peak_w = float(np.percentile(widths, 90))
    width_floor = peak_w * 0.72
    med_w = float(np.median(widths[widths >= width_floor]))
    width_cap = med_w * 1.02
    width_ceil = peak_w * 1.10
    debug["peak_width_px"] = round(peak_w, 1)

    runs: List[List[Tuple[float, float, float, float]]] = []
    current: List[Tuple[float, float, float, float]] = []
    for st in stations:
        wd = st[3]
        ok = wd >= width_floor and wd >= 10 and wd <= width_ceil
        if ok:
            if current and st[0] - current[-1][0] > step * 3:
                runs.append(current)
                current = []
            current.append(st)
        else:
            if current:
                runs.append(current)
                current = []
    if current:
        runs.append(current)

    def _run_uniformity(run: List[Tuple[float, float, float, float]]) -> float:
        w = np.array([r[3] for r in run], dtype=np.float64)
        return float(np.std(w) / max(np.mean(w), 1.0))

    best_run: List[Tuple[float, float, float, float]] = []
    best_score = -1.0
    max_len_px = 130.0
    for run in runs:
        if len(run) < 6:
            continue
        span = run[-1][0] - run[0][0]
        if span > max_len_px:
            continue
        uni = _run_uniformity(run)
        if uni > 0.28:
            continue
        score = span * float(np.median([r[3] for r in run])) / (1.0 + uni * 3)
        if score > best_score:
            best_score = score
            best_run = run

    if not best_run:
        best_run = max(runs, key=lambda r: len(r)) if runs else stations

    top_pts: List[np.ndarray] = []
    bot_pts: List[np.ndarray] = []
    kept = 0
    for s_val, t_top, t_bot, wd in best_run:
        t_bot = min(t_bot, t_top + width_cap)
        top_pts.append(mean + major * s_val + perp * t_top)
        bot_pts.append(mean + major * s_val + perp * t_bot)
        kept += 1

    debug["profile_stations"] = kept
    if kept < 8:
        return None, init_contour, {**debug, "refine_skipped": "stations_after_trim"}

    mask = np.zeros((h, w), np.uint8)
    for i in range(len(top_pts) - 1):
        quad = np.array(
            [top_pts[i], top_pts[i + 1], bot_pts[i + 1], bot_pts[i]],
            dtype=np.int32,
        )
        cv2.fillConvexPoly(mask, quad, 255)

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        1,
    )
    contour = _largest_contour(mask, 250)
    if contour is None:
        return None, init_contour, {**debug, "refine_skipped": "empty_refined_mask"}

    cy = cv2.boundingRect(contour)[1] + cv2.boundingRect(contour)[3] / 2
    if cy < cable_y0 - 80 or cy > cable_y1 + 80:
        return None, init_contour, {**debug, "refine_skipped": "off_band"}

    area = cv2.contourArea(contour)
    x, y, bw, bh = cv2.boundingRect(contour)
    debug["fill_ratio"] = round(area / max(bw * bh, 1), 3)
    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [contour], -1, 255, -1)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    return out, contour, debug


def _estimate_sleeve_left_x(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
) -> int:
    """Matte-tube left edge (read-only) to place welding splice left of the heat-shrink body."""
    h, w = img.shape[:2]
    ref = load_sleeve_reference()
    if ref is None:
        return int(w * 0.68)
    tube = _matte_tube_mask_reference(img, ref)
    y_m0 = max(0, cable_y0 - 35)
    y_m1 = min(h, cable_y1 + 35)
    band = np.zeros((h, w), np.uint8)
    band[y_m0:y_m1, :] = 255
    masked = cv2.bitwise_and(tube, band)
    cnts, _ = cv2.findContours(masked, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_x = int(w * 0.68)
    best_key = -1.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 4000:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong < 2.0:
            continue
        key = area * min(elong, 10.0) * (x + bw * 0.35)
        if key > best_key:
            best_key = key
            best_x = x
    return best_x


def _band_seed_rects(
    x: int,
    y: int,
    bw: int,
    bh: int,
    copper_strip: Optional[np.ndarray] = None,
    cable_y0: int = 0,
) -> List[Tuple[int, int, int, int]]:
    """Crop wide/tall copper streaks to windows that cover the barrel welding splice only."""
    rects: List[Tuple[int, int, int, int]] = []
    y_mid = y + bh // 2
    if copper_strip is not None and copper_strip.size > 0:
        row_lim = min(bh, max(72, cable_y0 + 185 - y))
        sub = copper_strip[:row_lim]
        center = sub[:, bw // 4 : max(bw // 4 + 1, 3 * bw // 4)]
        if center.size > 0:
            rs = np.convolve(
                np.sum(center > 0, axis=1).astype(np.float64),
                np.ones(9, dtype=np.float64) / 9.0,
                mode="same",
            )
            peak = int(np.argmax(rs))
            if rs[peak] >= 12:
                y_mid = y + peak

    if bw > 420:
        win_w = min(200, max(130, int(bw * 0.30)))
        bh_c = min(bh, 52)
        y_c = max(y, min(y + bh - bh_c, y_mid - bh_c // 2))
        for frac in (0.52, 0.44, 0.60):
            cx = x + int(bw * frac)
            x_c = max(x, cx - win_w // 2)
            bw_c = min(x + bw - x_c, win_w)
            rects.append((x_c, y_c, bw_c, bh_c))
        return rects
    if bh > 130 and bw >= 100:
        bh_c = min(bh, 80)
        y_c = max(y, min(y + bh - bh_c, y_mid - bh_c // 2))
        rects.append((x, y_c, bw, bh_c))
        return rects
    rects.append((x, y, bw, bh))
    return rects


def _assembly_welding_splice_from_wide_band(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    copper_wide: np.ndarray,
) -> Optional[Tuple[float, np.ndarray, Tuple[int, int, int, int]]]:
    """
    Dull copper welding splice often appears only as a wide horizontal band in the center
    assembly (narrow HSV misses it; edge blobs are wire tips or sleeve mouth).
    """
    h, w = img.shape[:2]
    sleeve_left = _estimate_sleeve_left_x(img, cable_y0, cable_y1)
    welding_splice_x_hi = max(int(w * 0.30), sleeve_left - 35)
    welding_splice_x_lo = int(w * 0.20)
    x0, x1 = int(w * 0.08), int(w * 0.78)
    y_m0 = max(0, cable_y0 - 25)
    y_m1 = min(h, cable_y1 + 25)
    roi = copper_wide[y_m0:y_m1, x0:x1]
    if roi.size == 0:
        return None
    cnts, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: Optional[Tuple[float, np.ndarray, Tuple[int, int, int, int]]] = None
    best_key = -1.0
    target_cx = (welding_splice_x_lo + welding_splice_x_hi) / 2.0

    def _try_rect(rx: int, ry: int, rbw: int, rbh: int) -> None:
        nonlocal best, best_key
        if rbw < 60 or rbh < 10:
            return
        seed = np.zeros((h, w), np.uint8)
        seed[ry : ry + rbh, rx : rx + rbw] = 255
        cnts_s, _ = cv2.findContours(seed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts_s:
            return
        c_full = max(cnts_s, key=cv2.contourArea)
        barrel_band = cv2.bitwise_and(_welding_splice_barrel_mask(img), copper_wide)
        _, refined_contour, _ = _refine_full_welding_splice_mask(
            img, c_full, cable_y0, cable_y1, copper_mask=barrel_band
        )
        if refined_contour is None or not _welding_splice_color_valid(img, refined_contour):
            return
        fx, fy, fbw, fbh = cv2.boundingRect(refined_contour)
        cx = fx + fbw / 2
        if cx < welding_splice_x_lo or cx > welding_splice_x_hi:
            return
        compact_barrel = 40 <= fbw <= 100 and 12 <= fbh <= 66
        if not _refined_welding_splice_geometry_ok(c_full, refined_contour):
            if not _refined_welding_splice_barrel_ok(refined_contour) and not compact_barrel:
                return
        ref_area = cv2.contourArea(refined_contour)
        barrel_bonus = 3.5 if compact_barrel else 1.0
        x_pen = abs(cx - target_cx) / max(welding_splice_x_hi - welding_splice_x_lo, 1)
        key = (
            ref_area
            * barrel_bonus
            * _copper_fraction_in_contour(img, refined_contour)
            / (1.0 + x_pen * 1.8)
        )
        if key > best_key:
            best_key = key
            best = (key, refined_contour, (fx, fy, fbw, fbh))

    for c in cnts:
        area = cv2.contourArea(c)
        if area < 1200:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        x += x0
        y += y_m0
        if bw < 80 or bh < 10:
            continue
        if bw < 420 and bh > 130:
            continue
        if bw / max(bh, 1) < 1.5 and bw < 200:
            continue
        strip = copper_wide[y : y + bh, x : x + bw]
        for rx, ry, rbw, rbh in _band_seed_rects(x, y, bw, bh, strip, cable_y0):
            _try_rect(rx, ry, rbw, rbh)
    return best


def _welding_splice_candidates_in_left_roi(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
) -> List[Tuple[float, np.ndarray, Tuple[int, int, int, int]]]:
    """Find compact copper barrel in the left assembly region (orange + dull copper)."""
    h, w = img.shape[:2]
    max_welding_splice_bw = min(380, int(w * 0.30))
    x1 = int(w * 0.88)
    y_m0 = max(0, cable_y0 - 30)
    y_m1 = min(h, cable_y1 + 30)
    roi = img[y_m0:y_m1, 0:x1]
    if roi.size == 0:
        return []
    rh = roi.shape[0]
    copper = _copper_mask(roi)
    copper = cv2.morphologyEx(
        copper, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), 1,
    )
    cnts, _ = cv2.findContours(copper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: List[Tuple[float, np.ndarray, Tuple[int, int, int, int]]] = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 450 or area > 12000:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 42 or bw > max_welding_splice_bw or bh < 10 or bh > 52:
            continue
        if bw < bh * 0.85:
            continue
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong > 8.0 or elong < 1.15:
            continue
        # map to full image coords
        c_full = c.copy()
        c_full[:, 0, 0] += 0
        c_full[:, 0, 1] += y_m0
        copper_frac = _copper_fraction_in_contour(img, c_full)
        if copper_frac < 0.22:
            continue
        if not _welding_splice_color_valid(img, c_full):
            continue
        cx = x + bw / 2
        cy = y + bh / 2 + y_m0
        compact = area / max(bw * bh, 1)
        barrel_bonus = 2.5 if 48 <= bw <= 125 and 14 <= bh <= 42 else 1.0
        score = (
            area * min(elong, 6.0) * (0.5 + compact) * barrel_bonus
            * (0.35 + copper_frac) / (1.0 + abs(cy - (cable_y0 + cable_y1) / 2) * 0.002)
        )
        out.append((score, c_full, (x, y + y_m0, bw, bh)))
    return out


def detect_welding_splice_mask(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    h, w = img.shape[:2]
    debug: Dict[str, Any] = {}

    y_m0 = max(0, cable_y0 - 40)
    y_m1 = min(h, cable_y1 + 40)
    band = np.zeros((h, w), np.uint8)
    band[y_m0:y_m1, :] = 255
    copper_wide = cv2.bitwise_and(_copper_mask(img), band)
    copper_seed = cv2.bitwise_and(_copper_mask_narrow(img), band)

    cnts, _ = cv2.findContours(copper_seed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: List[Tuple[float, np.ndarray, Tuple[int, int, int, int]]] = []
    band_mid_x = w * 0.5
    max_welding_splice_bw = min(380, int(w * 0.30))
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 280 or area > 9000:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw > w * 0.40 or bw < 38 or bw > max_welding_splice_bw:
            continue
        if bh < 8 or bh > 48:
            continue
        cy = y + bh / 2
        cx = x + bw / 2
        if cy < cable_y0 - 80 or cy > cable_y1 + 80:
            continue
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong > 12 or elong < 1.1:
            continue
        if bw < bh * 0.9:
            continue
        if cx > w * 0.78 and bw < 160:
            continue
        compact = area / max(bw * bh, 1)
        x_pen = abs(cx - band_mid_x) / w
        barrel_bonus = 2.2 if 50 <= bw <= 110 and 12 <= bh <= 40 else 1.0
        aspect_bonus = 1.0 + max(0.0, (bw / max(bh, 1) - 1.0) * 0.3)
        copper_frac = _copper_fraction_in_contour(img, c)
        if copper_frac < 0.12:
            continue
        left_bonus = 1.35 if cx < w * 0.52 else 0.75
        score = (
            area * min(elong, 6.0) * (0.5 + compact) * aspect_bonus
            * barrel_bonus * left_bonus * (0.4 + copper_frac)
            / (1.0 + x_pen * 2.5)
        )
        candidates.append((score, c, (x, y, bw, bh)))

    if not candidates:
        cnts, _ = cv2.findContours(copper_wide, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if area < 280 or area > 9000:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            if bw > w * 0.40 or bw < 38 or bw > max_welding_splice_bw:
                continue
            if bh < 8 or bh > 48:
                continue
            cy = y + bh / 2
            cx = x + bw / 2
            if cy < cable_y0 - 80 or cy > cable_y1 + 80:
                continue
            elong = max(bw, bh) / max(1, min(bw, bh))
            if elong > 12 or elong < 1.1 or bw < bh * 0.9:
                continue
            if cx > w * 0.78 and bw < 160:
                continue
            compact = area / max(bw * bh, 1)
            x_pen = abs(cx - w * 0.5) / w
            barrel_bonus = 2.2 if 50 <= bw <= 110 and 12 <= bh <= 40 else 1.0
            aspect_bonus = 1.0 + max(0.0, (bw / max(bh, 1) - 1.0) * 0.3)
            copper_frac = _copper_fraction_in_contour(img, c)
            if copper_frac < 0.12:
                continue
            left_bonus = 1.35 if cx < w * 0.52 else 0.75
            score = (
                area * min(elong, 6.0) * (0.5 + compact) * aspect_bonus
                * barrel_bonus * left_bonus * (0.4 + copper_frac)
                / (1.0 + x_pen * 2.5)
            )
            candidates.append((score, c, (x, y, bw, bh)))
        debug["seed_source"] = "wide_fallback"

    if not candidates:
        candidates = _welding_splice_candidates_in_left_roi(img, cable_y0, cable_y1)
        if candidates:
            debug["seed_source"] = "left_roi"

    # Supplement with wide-mask barrel copper (dull metal often missing from narrow HSV).
    wide_cnts, _ = cv2.findContours(copper_wide, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    seen_boxes: set = {t[2] for t in candidates}
    for c in wide_cnts:
        area = cv2.contourArea(c)
        if area < 280 or area > 12000:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 42 or bw > max_welding_splice_bw or bh < 10 or bh > 52:
            continue
        if bw < bh * 0.85:
            continue
        cx = x + bw / 2
        if cx < w * 0.18 or cx > w * 0.88:
            continue
        if not (45 <= bw <= 125 and 12 <= bh <= 45):
            continue
        box = (x, y, bw, bh)
        if box in seen_boxes:
            continue
        if not _welding_splice_color_valid(img, c):
            continue
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong > 12 or elong < 1.1:
            continue
        cy = y + bh / 2
        if cy < cable_y0 - 80 or cy > cable_y1 + 80:
            continue
        compact = area / max(bw * bh, 1)
        copper_frac = _copper_fraction_in_contour(img, c)
        barrel_bonus = 3.0
        score = (
            area * min(elong, 6.0) * (0.5 + compact) * barrel_bonus
            * (0.4 + copper_frac)
        )
        candidates.append((score, c, box))
        seen_boxes.add(box)
    if candidates and debug.get("seed_source") != "wide_fallback":
        debug["wide_barrel_added"] = True

    if _is_likely_sleeve_only_scene(img, cable_y0, cable_y1):
        band_welding_splice = None
    else:
        band_welding_splice = _assembly_welding_splice_from_wide_band(img, cable_y0, cable_y1, copper_wide)
    if band_welding_splice is not None:
        candidates = [band_welding_splice]
        debug["assembly_band_welding_splice"] = True
        debug["welding_splice_band"] = "assembly_band"
        debug.setdefault("seed_source", "wide_band")
    elif not candidates:
        return None, {"reason": "no_copper_contour_in_cable_band"}
    else:
        debug.setdefault("seed_source", "narrow")

    if band_welding_splice is None:
        assembly = [
            t for t in candidates
            if w * 0.18 < t[2][0] + t[2][2] / 2 < w * 0.88
        ]
        barrel = [
            t for t in assembly
            if 45 <= t[2][2] <= 125 and 10 <= t[2][3] <= 55
        ]
        center_barrel = [
            t for t in barrel
            if w * 0.22 < t[2][0] + t[2][2] / 2 < w * 0.72
        ]
        if center_barrel:
            candidates = center_barrel
            debug["welding_splice_band"] = "center_barrel"
        elif barrel:
            candidates = barrel
            debug["welding_splice_band"] = "barrel_assembly"
        elif assembly:
            candidates = assembly
            debug["welding_splice_band"] = "assembly"
    candidates.sort(key=lambda t: t[0], reverse=True)
    debug["candidates"] = len(candidates)

    mask_out = np.zeros((h, w), np.uint8)
    primary: Optional[np.ndarray] = None
    best_score = -1.0
    for score, cand, (x, y, bw, bh) in candidates:
        if not _welding_splice_color_valid(img, cand):
            continue
        cx = x + bw / 2
        if cx < w * 0.08 and bw < 130:
            continue
        if score > best_score:
            best_score = score
            primary = cand
    if primary is None:
        primary = candidates[0][1]
    if band_welding_splice is not None and primary is band_welding_splice[1]:
        debug["used_assembly_band_welding_splice"] = True
        debug["welding_splice_band"] = "assembly_band"
    px0, py0, pw, ph = cv2.boundingRect(primary)
    cx0, cy0 = px0 + pw / 2, py0 + ph / 2
    cv2.drawContours(mask_out, [primary], -1, 255, -1)
    merge_x = min(max(pw, bw) * 0.55 + 45, 220.0)
    for _, c, _ in candidates[1:8]:
        x, y, bw, bh = cv2.boundingRect(c)
        cx, cy = x + bw / 2, y + bh / 2
        if abs(cx - cx0) > merge_x:
            continue
        if abs(cy - cy0) <= max(ph, bh) + 22:
            cv2.drawContours(mask_out, [c], -1, 255, -1)
    seed_contour = _largest_contour(mask_out, 250)
    if seed_contour is None:
        return None, {"reason": "merge_failed"}

    sx, sy, sbw, sbh = cv2.boundingRect(seed_contour)
    seed_area = cv2.contourArea(seed_contour)
    use_refine = sbw <= 100 and seed_area < 4500
    debug["seed_aabb"] = [int(sx), int(sy), int(sbw), int(sbh)]

    if use_refine and not debug.get("used_assembly_band_welding_splice"):
        barrel = cv2.bitwise_and(_welding_splice_barrel_mask(img), band)
        refined_mask, refined_contour, refine_dbg = _refine_full_welding_splice_mask(
            img, seed_contour, cable_y0, cable_y1, copper_mask=barrel
        )
        debug.update(refine_dbg)
        if refined_mask is not None and refined_contour is not None:
            if not _refined_welding_splice_geometry_ok(seed_contour, refined_contour):
                debug["refine_rejected"] = "bad_geometry"
            elif _welding_splice_color_valid(img, refined_contour):
                refined_contour = _strip_insulation_from_welding_splice(img, refined_contour)
                out = _contour_to_mask(refined_contour, h, w)
                x, y, bw, bh = cv2.boundingRect(refined_contour)
                debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
                return out, debug
            else:
                debug["refine_rejected"] = "not_copper_colored"

    if debug.get("used_assembly_band_welding_splice") and _welding_splice_color_valid(img, primary):
        seed_band = primary
        primary, fin_dbg = _finalize_welding_splice_contour(
            img, primary, cable_y0, cable_y1, band
        )
        debug.update(fin_dbg)
        if not _welding_splice_color_valid(img, primary):
            primary = seed_band
            debug["finalize_reverted"] = "color"
        if not _welding_splice_color_valid(img, primary):
            return None, {**debug, "reason": "welding_splice_failed_color_check"}
        px, py, pw, ph = cv2.boundingRect(primary)
        compact_ok = 40 <= pw <= 100 and 12 <= ph <= 66
        if not (
            _refined_welding_splice_barrel_ok(primary)
            or _refined_welding_splice_geometry_ok(seed_band, primary)
            or compact_ok
        ):
            primary = seed_band
            debug["finalize_reverted"] = "geometry"
        out = np.zeros((h, w), np.uint8)
        cv2.drawContours(out, [primary], -1, 255, -1)
        x, y, bw, bh = cv2.boundingRect(primary)
        debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
        debug["refined_full_welding_splice"] = True
        return out, debug

    seed_contour = _strip_insulation_from_welding_splice(img, seed_contour)
    cx_seed = sx + sbw / 2
    if (sbw > 95 or sbh > 50) and w * 0.20 < cx_seed < w * 0.72:
        seed_contour, fin_dbg = _finalize_welding_splice_contour(
            img, seed_contour, cable_y0, cable_y1, band
        )
        debug.update(fin_dbg)
    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [seed_contour], -1, 255, -1)
    if not _welding_splice_color_valid(img, seed_contour):
        return None, {**debug, "reason": "welding_splice_failed_color_check"}
    x, y, bw, bh = cv2.boundingRect(seed_contour)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    debug["refined_full_welding_splice"] = debug.get("welding_splice_refined", False)
    return out, debug


def _detect_sleeve_mask_blob(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    welding_splice_contour: Optional[np.ndarray],
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Fallback: elongated black blob right of welding splice with tail trim."""
    h, w = img.shape[:2]
    debug: Dict[str, Any] = {"method": "blob_fallback"}
    if welding_splice_contour is None:
        return None, debug
    x0, y0, cw, ch = cv2.boundingRect(welding_splice_contour)
    search_x0 = max(0, x0 + cw - 30)
    search_x1 = min(w, search_x0 + int(140 * 5.1))
    row_cy = y0 + ch / 2
    y_m0 = max(0, int(row_cy - ch * 2.5 - 18))
    y_m1 = min(h, int(row_cy + ch * 2.5 + 18))
    roi = img[y_m0:y_m1, search_x0:search_x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    colored = cv2.inRange(hsv, np.array([25, 45, 40]), np.array([130, 255, 255]))
    sleeve = _build_tube_mask(roi)
    sleeve = cv2.morphologyEx(
        sleeve, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), 2
    )
    cnts, _ = cv2.findContours(sleeve, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_score = -1.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 500:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong < 1.8:
            continue
        if max(bw, bh) < 35 or min(bw, bh) < 10:
            continue
        score = area * min(elong, 5.0) / (1.0 + x * 0.005)
        if score > best_score:
            best_score = score
            best = c
    if best is None:
        return None, {**debug, "reason": "blob_not_found"}
    local = np.zeros(roi.shape[:2], np.uint8)
    cv2.drawContours(local, [best], -1, 255, -1)
    col_black = local.sum(axis=0).astype(np.float64)
    col_color = colored.sum(axis=0).astype(np.float64)
    trim = local.shape[1]
    for col in range(local.shape[1] - 1, 0, -1):
        if col_black[col] < 40:
            continue
        if col_color[col] > col_black[col] * 0.4:
            trim = col
            break
    local[:, trim:] = 0
    full = np.zeros((h, w), np.uint8)
    full[y_m0:y_m1, search_x0:search_x1] = local
    contour = _largest_contour(full, 350)
    if contour is None:
        return None, debug
    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [contour], -1, 255, -1)
    x, y, bw, bh = cv2.boundingRect(contour)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    return out, debug


def _tube_mask(
    img: np.ndarray,
    morph_close_iters: int = 0,
    gray_thr: int = 115,
) -> np.ndarray:
    """
    Matte dark tube (heat-shrink): dark-to-mid-gray, low saturation.
    Measured on real sleeve: gray≈31–119 mean≈84, HSV V≈95, S≈47.
    Threshold raised from 62 to 115 to capture charcoal/slate sleeves.
    Excludes colored wires (blue, green, yellow, red) by saturation gate.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    dark = (gray < gray_thr).astype(np.uint8) * 255

    colored = cv2.inRange(hsv,
                          np.array([20, 55, 40], dtype=np.uint8),
                          np.array([135, 255, 255], dtype=np.uint8))
    red1 = cv2.inRange(hsv,
                       np.array([0, 100, 60], dtype=np.uint8),
                       np.array([8, 255, 255], dtype=np.uint8))
    red2 = cv2.inRange(hsv,
                       np.array([168, 100, 60], dtype=np.uint8),
                       np.array([180, 255, 255], dtype=np.uint8))
    not_colored = cv2.bitwise_not(cv2.bitwise_or(colored, cv2.bitwise_or(red1, red2)))

    tube = cv2.bitwise_and(dark, not_colored)

    if morph_close_iters > 0:
        tube = cv2.morphologyEx(
            tube, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)),
            iterations=morph_close_iters,
        )
    return tube


def _build_tube_mask(img: np.ndarray) -> np.ndarray:
    return _tube_mask(img, morph_close_iters=2, gray_thr=115)


def load_sleeve_reference() -> Optional[Dict[str, Any]]:
    """Load matte black sleeve color/geometry profile (capture5 + capture8)."""
    global _SLEEVE_REF_CACHE
    if _SLEEVE_REF_CACHE is not None:
        return _SLEEVE_REF_CACHE
    if not _SLEEVE_REF_JSON.is_file():
        return None
    try:
        data = json.loads(_SLEEVE_REF_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    _SLEEVE_REF_CACHE = data
    return data


def _matte_tube_mask_reference(
    img: np.ndarray,
    ref: Dict[str, Any],
    band: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Segment matte RBK-3 tube using reference gray/sat limits from golden masks.
    Location-independent: any elongated dark low-sat blob on white passes to scoring.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    colored = cv2.inRange(
        hsv,
        np.array([20, 55, 40], dtype=np.uint8),
        np.array([135, 255, 255], dtype=np.uint8),
    )
    gray_thr = int(ref.get("gray_max", ref.get("gray_seed", 95)))
    sat_thr = int(ref.get("sat_max", 72))
    tube = (
        (gray < gray_thr).astype(np.uint8)
        & (hsv[:, :, 1] < sat_thr).astype(np.uint8)
        & (colored == 0).astype(np.uint8)
    ) * 255
    ch = int(ref.get("morph_close_h", 25))
    cv_ = int(ref.get("morph_close_v", 5))
    tube = cv2.morphologyEx(
        tube, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (ch, cv_)),
    )
    if band is not None:
        tube = cv2.bitwise_and(tube, band)
    return tube


def _blob_fill_ratio(contour: np.ndarray) -> float:
    """Area / AABB fill; convex hull tolerates RBK-3 text and interior gaps."""
    area = cv2.contourArea(contour)
    x, y, bw, bh = cv2.boundingRect(contour)
    if bw * bh < 1:
        return 0.0
    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    return max(area, hull_area) / float(bw * bh)


def _color_match_reference(
    img: np.ndarray,
    contour: np.ndarray,
    ref: Dict[str, Any],
) -> float:
    """0..1 score: how well interior pixels match reference capture5/8 profile."""
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    if cv2.countNonZero(mask) < 80:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    g = gray[mask > 0].astype(np.float64)
    s = hsv[:, :, 1][mask > 0].astype(np.float64)
    g50 = float(np.median(g))
    s50 = float(np.median(s))
    tgt_g = float(ref.get("gray_p50_target", 46))
    tgt_s = float(ref.get("sat_median_target", 27))
    g_ok = 1.0 - min(abs(g50 - tgt_g) / 35.0, 1.0)
    s_ok = 1.0 - min(abs(s50 - tgt_s) / 40.0, 1.0)
    g_cap = float(ref.get("gray_max", 73))
    frac_in = float((g <= g_cap + 6).mean())
    return max(0.0, 0.45 * g_ok + 0.35 * s_ok + 0.20 * frac_in)


def _score_reference_tube_blob(
    contour: np.ndarray,
    img: np.ndarray,
    ref: Dict[str, Any],
    cable_y0: int,
    cable_y1: int,
    w: int,
    h: int,
) -> float:
    area = cv2.contourArea(contour)
    x, y, bw, bh = cv2.boundingRect(contour)
    min_area = max(3500, int(ref.get("min_area_px", 8500) * 0.42))
    if area < min_area:
        return -1.0
    elong = max(bw, bh) / max(1, min(bw, bh))
    elong_min = float(ref.get("elong_min", 2.5))
    if elong < elong_min or elong > ref.get("elong_max", 12.0):
        return -1.0
    if max(bw, bh) < ref.get("min_length_px", 250):
        return -1.0
    if min(bw, bh) < ref.get("min_height_px", 12):
        return -1.0
    max_h = int(ref.get("max_height_px", 120))
    profs = ref.get("profiles", [])
    if profs:
        max_h = int(max(p["aabb"][3] for p in profs) * 1.72)
    if min(bw, bh) > max_h:
        return -1.0
    if bw > w * ref.get("max_width_frac", 0.55):
        return -1.0
    if y < max(cable_y0 - 80, int(h * 0.08)):
        return -1.0
    color = _color_match_reference(img, contour, ref)
    fill = _blob_fill_ratio(contour)
    fill_min = float(ref.get("fill_min", 0.38))
    if color > 0.72 and fill >= fill_min - 0.05:
        fill_min -= 0.05
    if fill < fill_min or fill > ref.get("fill_max", 0.88):
        return -1.0
    cy = y + bh / 2
    if cy < cable_y0 - 60 or cy > cable_y1 + 60:
        return -1.0
    band_mid = (cable_y0 + cable_y1) / 2
    y_pen = abs(cy - band_mid) / max(cable_y1 - cable_y0, 1)
    return area * min(elong, 10.0) * (0.55 + 0.45 * color) / (1.0 + y_pen * 0.15)


def _pick_reference_seed_contour(
    contours: Sequence[np.ndarray],
    img: np.ndarray,
    ref: Dict[str, Any],
    cable_y0: int,
    cable_y1: int,
    w: int,
    h: int,
) -> Optional[np.ndarray]:
    """
    When strict scoring rejects all blobs (e.g. RBK-3 text lowers fill), keep the
    best elongated matte candidate so _finalize_reference_mask can clip wire ends.
    """
    best: Optional[np.ndarray] = None
    best_key = -1.0
    min_area = max(2500, int(ref.get("min_area_px", 8500) * 0.30))
    ref_h = float(np.median([p["aabb"][3] for p in ref.get("profiles", [])]) or 87.0)
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong < float(ref.get("elong_min", 2.4)) * 0.85:
            continue
        if min(bw, bh) > ref_h * 1.85:
            continue
        color = _color_match_reference(img, c, ref)
        if color < 0.48:
            continue
        cy = y + bh / 2
        if cy < cable_y0 - 80 or cy > cable_y1 + 80:
            continue
        key = area * min(elong, 9.0) * (0.4 + 0.6 * color)
        if key > best_key:
            best_key = key
            best = c
    return best


def _matte_column_at(
    gray: np.ndarray,
    hsv: np.ndarray,
    center: np.ndarray,
) -> bool:
    """True when the station center lies on solid matte sleeve (not wires/background)."""
    sat_m, min_g, frac25 = _strip_sleeve_body_metrics(gray, hsv, center)
    return _is_matte_sleeve_station(sat_m, min_g, frac25, 0.0, 80.0) or (
        sat_m < 58.0 and min_g < 42.0 and frac25 < 0.08
    )


def _clip_reference_mask_matte_span(
    img: np.ndarray,
    mask: np.ndarray,
    contour: np.ndarray,
    ref: Dict[str, Any],
    debug: Dict[str, Any],
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Trim both ends to the longest contiguous matte-sleeve run along the tube axis.
    Removes wire ends and dark gaps that inflate length (capture5/8 profile).
    """
    h, w = img.shape[:2]
    mean, major, perp, s_min, s_max = _contour_axis_frame(contour)
    if major[0] < 0:
        major = -major
        perp = -perp
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    step = 2.0
    flags: List[bool] = []
    s_vals: List[float] = []
    s = s_min
    while s <= s_max + step * 0.5:
        center = mean + major * s
        flags.append(_matte_column_at(gray, hsv, center))
        s_vals.append(s)
        s += step

    best_i, best_j, best_len = 0, 0, 0
    i = 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < len(flags) and flags[j]:
            j += 1
        if j - i > best_len:
            best_i, best_j, best_len = i, j, j - i
        i = j

    if best_len < 8:
        return mask, contour, debug

    s_lo = s_vals[best_i]
    s_hi = s_vals[best_j - 1]
    debug["matte_span_s"] = [round(s_lo, 1), round(s_hi, 1)]
    debug["matte_span_px"] = round(s_hi - s_lo, 1)

    half_cap = 48.0
    profs = ref.get("profiles", [])
    if profs:
        half_cap = float(np.median([p["aabb"][3] for p in profs])) * 0.52

    clip = np.zeros((h, w), np.uint8)
    s_cur = s_lo
    while s_cur <= s_hi + step * 0.5:
        center = mean + major * s_cur
        for t in np.linspace(-half_cap, half_cap, max(12, int(half_cap))):
            pt = center + perp * t
            px, py = int(round(pt[0])), int(round(pt[1]))
            if 0 <= px < w and 0 <= py < h and gray[py, px] < int(ref.get("gray_cap", 96)):
                clip[py, px] = 255
        s_cur += step

    clip = cv2.morphologyEx(
        clip, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)), 1,
    )
    seed_c = _largest_contour(clip, 500)
    if seed_c is None:
        return mask, contour, debug

    refined_mask, refined_contour, rdbg = _refine_full_tube_mask(img, seed_c)
    debug.update({f"clip_{k}": v for k, v in rdbg.items() if k not in debug})
    if refined_mask is None or refined_contour is None:
        return mask, contour, debug

    gray_cap = int(ref.get("gray_cap", 96))
    refined_mask = refined_mask.copy()
    refined_mask[gray >= gray_cap] = 0
    refined_contour = _largest_contour(refined_mask, 800)
    if refined_contour is None:
        return mask, contour, debug
    return refined_mask, refined_contour, debug


def _finalize_reference_mask(
    img: np.ndarray,
    seed_contour: np.ndarray,
    ref: Dict[str, Any],
    debug: Dict[str, Any],
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    h, w = img.shape[:2]
    refined_mask, refined_contour, refine_dbg = _refine_full_tube_mask(img, seed_contour)
    debug.update(refine_dbg)
    gray_cap = int(ref.get("gray_cap", 96))
    if refined_mask is not None and refined_contour is not None:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        refined_mask = refined_mask.copy()
        refined_mask[gray >= gray_cap] = 0
        refined_contour = _largest_contour(refined_mask, 800)
        if refined_contour is not None:
            refined_mask, refined_contour, clip_dbg = _clip_reference_mask_matte_span(
                img, refined_mask, refined_contour, ref, debug
            )
            debug.update(clip_dbg)
            out = np.zeros((h, w), np.uint8)
            cv2.drawContours(out, [refined_contour], -1, 255, -1)
            x, y, bw, bh = cv2.boundingRect(refined_contour)
            debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
            debug["color_match"] = round(_color_match_reference(img, refined_contour, ref), 3)
            return out, debug
    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [seed_contour], -1, 255, -1)
    x, y, bw, bh = cv2.boundingRect(seed_contour)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    debug["refined_full_tube"] = False
    return out, debug


def _detect_sleeve_mask_reference(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    search_x0: int = 0,
    search_x1: Optional[int] = None,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Detect full matte black sleeve using reference profile from capture5/capture8.
    Works for sleeve-only and assembly images (optional horizontal search window).
    """
    h, w = img.shape[:2]
    debug: Dict[str, Any] = {"method": "reference"}
    ref = load_sleeve_reference()
    if ref is None:
        return None, {**debug, "reason": "reference_missing"}

    debug["reference_sources"] = ref.get("sources", [])
    x1 = w if search_x1 is None else min(w, search_x1)
    x0 = max(0, search_x0)
    y_m0 = max(0, cable_y0 - 40)
    y_m1 = min(h, cable_y1 + 40)
    band = np.zeros((h, w), np.uint8)
    band[y_m0:y_m1, x0:x1] = 255

    tube = _matte_tube_mask_reference(img, ref, band)
    cnts, _ = cv2.findContours(tube, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ranked: List[Tuple[float, float, np.ndarray]] = []
    for c in cnts:
        sc = _score_reference_tube_blob(c, img, ref, cable_y0, cable_y1, w, h)
        if sc < 0:
            continue
        ranked.append((sc, _color_match_reference(img, c, ref), c))
    ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
    debug["candidates"] = len(ranked)
    if not ranked:
        best = _pick_reference_seed_contour(cnts, img, ref, cable_y0, cable_y1, w, h)
        if best is not None:
            debug["candidate_pick"] = "relaxed_matte_seed"
    else:
        best = ranked[0][2]
        if len(ranked) > 1 and ranked[1][0] > ranked[0][0] * 0.4:
            if ranked[1][1] > ranked[0][1] + 0.06:
                best = ranked[1][2]
                debug["candidate_pick"] = "color_match"
    debug["search_x"] = [x0, x1]
    if best is None:
        return None, {**debug, "reason": "no_reference_tube_blob"}

    seed = np.zeros((h, w), np.uint8)
    cv2.drawContours(seed, [best], -1, 255, -1)
    seed_contour = _largest_contour(seed, int(ref.get("min_area_px", 12500) * 0.5))
    if seed_contour is None:
        return None, {**debug, "reason": "reference_seed_empty"}

    return _finalize_reference_mask(img, seed_contour, ref, debug)


def _detect_sleeve_mask_reference_with_fallback(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    welding_splice_contour: Optional[np.ndarray] = None,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Reference tube detect; if a welding-splice-anchored search window is empty, retry full width.
    """
    h, w = img.shape[:2]
    search_x0 = 0
    if welding_splice_contour is not None:
        cx0, _, cxw, _ = cv2.boundingRect(welding_splice_contour)
        cx = cx0 + cxw / 2
        if cx < w * 0.72:
            search_x0 = max(0, cx0 + cxw - 40)
        elif cx > w * 0.75:
            search_x0 = 0
        else:
            search_x0 = max(0, cx0 + cxw - 120)

    mask, dbg = _detect_sleeve_mask_reference(
        img, cable_y0, cable_y1, search_x0=search_x0
    )
    if mask is None and search_x0 > 0:
        mask2, dbg2 = _detect_sleeve_mask_reference(img, cable_y0, cable_y1, search_x0=0)
        if mask2 is not None:
            dbg2["reference_retry"] = "full_width"
            return mask2, dbg2
    return mask, dbg


def _tube_mask_standalone(img: np.ndarray) -> np.ndarray:
    """Matte-dark tube seed; uses reference thresholds when available."""
    ref = load_sleeve_reference()
    if ref is not None:
        return _matte_tube_mask_reference(img, ref)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    colored = cv2.inRange(
        hsv,
        np.array([20, 55, 40], dtype=np.uint8),
        np.array([135, 255, 255], dtype=np.uint8),
    )
    dark = (gray < 110).astype(np.uint8) * 255
    low_sat = (hsv[:, :, 1] < 85).astype(np.uint8) * 255
    tube = cv2.bitwise_and(dark, cv2.bitwise_and(low_sat, cv2.bitwise_not(colored)))
    tube = cv2.morphologyEx(
        tube, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (41, 7)), iterations=1,
    )
    tube = cv2.morphologyEx(
        tube, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)), iterations=1,
    )
    return tube


def _contour_axis_frame(
    contour: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    pts = contour.reshape(-1, 2).astype(np.float64)
    mean = pts.mean(axis=0)
    centered = pts - mean
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    major = vh[0].astype(np.float64)
    perp = vh[1].astype(np.float64)
    if major[0] < 0:
        major = -major
        perp = -perp
    major /= np.linalg.norm(major)
    perp /= np.linalg.norm(perp)
    along = centered @ major
    return mean, major, perp, float(along.min()), float(along.max())


def _reference_tube_half_cap(ref: Optional[Dict[str, Any]]) -> float:
    if ref is None:
        return 45.0
    ep = ref.get("edge_profile") or {}
    if ep.get("half_width_median"):
        return float(ep["half_width_median"])
    profs = ref.get("profiles", [])
    if profs:
        return float(np.median([p["aabb"][3] for p in profs])) * 0.52
    return 45.0


def _tube_edges_along_perp(
    gray: np.ndarray,
    hsv: np.ndarray,
    center: np.ndarray,
    perp: np.ndarray,
    half_cap: float,
    gray_thr: float,
    ref: Optional[Dict[str, Any]],
) -> Optional[Tuple[float, float, float]]:
    """
    Find top/bottom tube edges on a perpendicular slice using reference gray/sat
    limits from capture5/8. Ignores interior text edges; caps span to golden OD.
    """
    h, w = gray.shape[:2]
    cx, cy = float(center[0]), float(center[1])
    sat_max = 72.0
    if ref is not None:
        sat_max = float(ref.get("sat_max", 72))
    scan = int(max(24, half_cap * 2.4))
    ts: List[float] = []
    for t in np.linspace(-scan, scan, scan * 2 + 1):
        px = int(round(cx + perp[0] * t))
        py = int(round(cy + perp[1] * t))
        if 0 <= px < w and 0 <= py < h:
            if gray[py, px] < gray_thr and hsv[py, px, 1] < sat_max:
                ts.append(t)
    if len(ts) < 5:
        return None
    arr = np.array(ts, dtype=np.float64)
    t_lo = float(np.percentile(arr, 8))
    t_hi = float(np.percentile(arr, 92))
    t_ctr = float(np.median(arr))
    half = min((t_hi - t_lo) / 2.0, half_cap)
    return t_ctr - half, t_ctr + half, t_ctr


def _refine_full_tube_mask(
    img: np.ndarray,
    init_contour: np.ndarray,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Dict[str, Any]]:
    """
    Rebuild a solid full-tube mask by marching along the tube axis and fitting
    top/bottom edges (shadow on the lower side is capped).
    """
    h, w = img.shape[:2]
    debug: Dict[str, Any] = {"refined_full_tube": True}
    mean, major, perp, s_min, s_max = _contour_axis_frame(init_contour)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    ref = load_sleeve_reference()

    seed = np.zeros((h, w), np.uint8)
    cv2.drawContours(seed, [init_contour], -1, 255, -1)
    samples = gray[seed > 0]
    if samples.size < 50:
        return None, init_contour, {**debug, "refine_skipped": "seed_too_small"}
    ref_cap = int(ref.get("gray_cap", 96)) if ref is not None else 96
    gray_thr = float(min(ref_cap, np.percentile(samples, 90) + 8))
    if ref is not None:
        gray_thr = min(gray_thr, float(ref.get("gray_max", 73)) + 18)
    debug["gray_thr"] = round(gray_thr, 1)

    half_cap = _reference_tube_half_cap(ref)
    debug["half_cap_px"] = round(half_cap, 1)

    stations: List[Tuple[float, float, float]] = []
    step = 2.0
    s = s_min
    while s <= s_max + step * 0.5:
        center = mean + major * s
        edges = _tube_edges_along_perp(gray, hsv, center, perp, half_cap, gray_thr, ref)
        if edges is not None:
            t_top, t_bot, _ = edges
            stations.append((s, t_top, t_bot))
        s += step

    if len(stations) < 12:
        return None, init_contour, {**debug, "refine_skipped": "too_few_stations"}

    ods = np.array([st[2] - st[1] for st in stations], dtype=np.float64)
    peak_od = float(np.percentile(ods, 90))
    od_floor = peak_od * 0.42
    med_od = float(np.median(ods))
    half_cap = min(med_od * 0.50, _reference_tube_half_cap(ref))
    debug["peak_od_px"] = round(peak_od, 1)
    debug["median_od_px"] = round(med_od, 1)
    debug["half_cap_px"] = round(half_cap, 1)

    top_pts: List[np.ndarray] = []
    bot_pts: List[np.ndarray] = []
    kept = 0
    for s_val, t_top, t_bot in stations:
        t_ctr = (t_top + t_bot) / 2.0
        t_top = t_ctr - half_cap
        t_bot = t_ctr + half_cap
        od = t_bot - t_top
        if od < od_floor:
            continue
        top_pts.append(mean + major * s_val + perp * t_top)
        bot_pts.append(mean + major * s_val + perp * t_bot)
        kept += 1

    debug["profile_stations"] = kept
    if kept < 10:
        return None, init_contour, {**debug, "refine_skipped": "stations_after_trim"}

    mask = np.zeros((h, w), np.uint8)
    for i in range(len(top_pts) - 1):
        quad = np.array(
            [top_pts[i], top_pts[i + 1], bot_pts[i + 1], bot_pts[i]],
            dtype=np.int32,
        )
        cv2.fillConvexPoly(mask, quad, 255)

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
        1,
    )
    contour = _largest_contour(mask, 800)
    if contour is None:
        return None, init_contour, {**debug, "refine_skipped": "empty_refined_mask"}

    area = cv2.contourArea(contour)
    x, y, bw, bh = cv2.boundingRect(contour)
    debug["fill_ratio"] = round(area / max(bw * bh, 1), 3)
    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [contour], -1, 255, -1)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    return out, contour, debug


def _detect_sleeve_mask_standalone(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Sleeve-only images: find the main heat-shrink tube without a copper welding splice anchor.
    """
    ref_mask, ref_dbg = _detect_sleeve_mask_reference(img, cable_y0, cable_y1)
    if ref_mask is not None:
        return ref_mask, ref_dbg

    h, w = img.shape[:2]
    debug: Dict[str, Any] = {"method": "standalone"}
    tube = _tube_mask_standalone(img)

    y_m0 = max(0, cable_y0 - 35)
    y_m1 = min(h, cable_y1 + 35)
    band = np.zeros((h, w), np.uint8)
    band[y_m0:y_m1, :] = 255
    tube_band = cv2.bitwise_and(tube, band)

    cnts, _ = cv2.findContours(tube_band, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    band_mid = (cable_y0 + cable_y1) / 2
    best: Optional[np.ndarray] = None
    best_score = -1.0
    candidates = 0

    for c in cnts:
        area = cv2.contourArea(c)
        if area < 400 or area > w * h * 0.12:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if y < h * 0.06:
            continue
        if bw > w * 0.55 or bh > h * 0.35:
            continue
        elong = max(bw, bh) / max(1, min(bw, bh))
        if elong < 1.8:
            continue
        if max(bw, bh) < 35 or min(bw, bh) < 8:
            continue
        cy = y + bh / 2
        if cy < cable_y0 - 50 or cy > cable_y1 + 50:
            continue
        candidates += 1
        y_pen = abs(cy - band_mid)
        edge_pen = 0.0
        if x <= 3:
            edge_pen += 0.5
        if x + bw >= w - 3:
            edge_pen += 0.5
        cx_blob = x + bw / 2
        x_cent = 1.0 - min(abs(cx_blob - w * 0.5) / max(w * 0.42, 1.0), 1.0)
        fill = area / max(bw * bh, 1)
        fill_bonus = 0.85 + 0.15 * min(fill, 0.95)
        score = (
            area * min(elong, 8.0) * (0.72 + 0.28 * x_cent) * fill_bonus
            / (1.0 + y_pen * 0.08 + edge_pen)
        )
        if score > best_score:
            best_score = score
            best = c

    debug["candidates"] = candidates
    if best is None:
        return None, {**debug, "reason": "no_standalone_tube_blob"}

    seed = np.zeros((h, w), np.uint8)
    cv2.drawContours(seed, [best], -1, 255, -1)
    seed_contour = _largest_contour(seed, 250)
    if seed_contour is None:
        return None, {**debug, "reason": "standalone_seed_empty"}

    ref = load_sleeve_reference()
    if ref is not None:
        out, fin_dbg = _finalize_reference_mask(img, seed_contour, ref, debug)
        debug.update(fin_dbg)
        if out is not None and cv2.countNonZero(out) > 500:
            return out, debug

    refined_mask, refined_contour, refine_dbg = _refine_full_tube_mask(
        img, seed_contour
    )
    debug.update(refine_dbg)
    if refined_mask is not None and refined_contour is not None:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        refined_mask[gray >= 96] = 0
        refined_contour = _largest_contour(refined_mask, 800)
        if refined_contour is not None:
            out = np.zeros((h, w), np.uint8)
            cv2.drawContours(out, [refined_contour], -1, 255, -1)
            x, y, bw, bh = cv2.boundingRect(refined_contour)
            debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
            return out, debug

    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [seed_contour], -1, 255, -1)
    x, y, bw, bh = cv2.boundingRect(seed_contour)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    debug["refined_full_tube"] = False
    return out, debug


def _welding_splice_anchor_reliable(img: np.ndarray, welding_splice_contour: np.ndarray) -> bool:
    """False when welding splice is likely a sleeve mouth / wire blob (skip axis anchor)."""
    x, y, bw, bh = cv2.boundingRect(welding_splice_contour)
    h, w = img.shape[:2]
    cx = x + bw / 2
    major = max(bw, bh)
    minor = min(bw, bh)
    if major < 100 or minor > 75:
        return False
    if cx > w * 0.75:
        return False
    if x + bw / 2 > w * 0.58 and bw < 130:
        return False
    if _matte_sleeve_fraction(img, welding_splice_contour) > 0.35:
        return False
    if _copper_fraction_in_contour(img, welding_splice_contour) < 0.22:
        return False
    return True


def _sleeve_mask_needs_reference_shape(
    contour: np.ndarray,
    img: np.ndarray,
) -> bool:
    """Axis mask too tall / shadow-heavy compared to golden capture5/8 tubes."""
    ref = load_sleeve_reference()
    if ref is None:
        return False
    profs = ref.get("profiles", [])
    if not profs:
        return False
    ref_h = float(np.median([p["aabb"][3] for p in profs]))
    ref_len = float(np.median([p["aabb"][2] for p in profs]))
    x, y, bw, bh = cv2.boundingRect(contour)
    major = float(max(bw, bh))
    minor = float(min(bw, bh))
    if minor > ref_h * 1.32:
        return True
    if major > ref_len * 1.55:
        return True
    if major < ref_len * 0.42:
        return True
    if _matte_sleeve_fraction(img, contour) < 0.45:
        return False
    return bh > ref_h * 1.22 and bw > ref_h * 2.5


def _sleeve_axis_measurement(
    contour: np.ndarray,
    px_per_mm: float,
    sleeve_dbg: Dict[str, Any],
) -> AxisMeasurement:
    """
    PCA length; OD from reference profile when refine debug is available
    (angled tubes have thin PCA height despite full OD).
    """
    meas = _pca_measurement(contour, px_per_mm)
    if sleeve_dbg.get("method") in ("reference", "reference_shape_fix", "reference_fallback"):
        od_px = sleeve_dbg.get("peak_od_px") or sleeve_dbg.get("median_od_px")
        half_cap = sleeve_dbg.get("half_cap_px")
        if half_cap:
            od_px = max(float(od_px or 0), float(half_cap) * 2.0)
        if od_px and float(od_px) > meas.height_px * 1.15:
            meas = AxisMeasurement(
                length_px=meas.length_px,
                height_px=float(od_px),
                axis_angle_deg=meas.axis_angle_deg,
                centroid=meas.centroid,
                major_unit=meas.major_unit,
                minor_unit=meas.minor_unit,
                _px_per_mm=px_per_mm,
            )
    return meas


def _welding_splice_axis(welding_splice_contour: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (centroid, major_unit, minor_unit) from welding splice contour."""
    pts = welding_splice_contour.reshape(-1, 2).astype(np.float64)
    mean = pts.mean(axis=0)
    centered = pts - mean
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    major = vh[0]
    minor = vh[1]
    # force major axis to point right (toward sleeve)
    if major[0] < 0:
        major = -major
        minor = -minor
    return mean, major / np.linalg.norm(major), minor / np.linalg.norm(minor)


def _sleeve_station_sample(
    gray: np.ndarray,
    colored: np.ndarray,
    center: np.ndarray,
    perp: np.ndarray,
    half_len: float = 85.0,
    dark_thr: int = 100,
) -> Tuple[float, float, np.ndarray]:
    """
    Sample one axial station: outer-boundary OD and color fraction on a line
    perpendicular to the welding splice axis. Recenters on the dark-pixel centroid so
    the profile follows curved/descending sleeves (welding splice axis alone drifts off
    the tube body after ~30–40 mm).
    """
    h, w = gray.shape[:2]
    cx_f, cy_f = float(center[0]), float(center[1])

    def _sample_at(cx: float, cy: float) -> Tuple[List[float], float]:
        pts: List[Tuple[float, int, int]] = []
        color_hits = 0
        for t in np.linspace(-half_len, half_len, int(half_len * 2) + 1):
            px = int(round(cx + perp[0] * t))
            py = int(round(cy + perp[1] * t))
            if 0 <= px < w and 0 <= py < h:
                pts.append((t, px, py))
                if colored[py, px] > 0:
                    color_hits += 1
        cf = color_hits / max(len(pts), 1)
        dark_t = [t for t, px, py in pts if gray[py, px] < dark_thr]
        return dark_t, cf

    dark_t, color_frac = _sample_at(cx_f, cy_f)
    if len(dark_t) < 2:
        return 0.0, color_frac, center

    t_mean = float(np.mean(dark_t))
    shift = min(max(t_mean, -half_len * 0.6), half_len * 0.6)
    if abs(shift) > 2.0:
        cx_f += perp[0] * shift
        cy_f += perp[1] * shift
        dark_t, color_frac = _sample_at(cx_f, cy_f)

    # Robust span: full min–max OD often includes shadow below the tube on white.
    if len(dark_t) >= 6:
        od_outer = float(np.percentile(dark_t, 90) - np.percentile(dark_t, 10))
    elif len(dark_t) >= 2:
        od_outer = float(dark_t[-1] - dark_t[0])
    else:
        od_outer = 0.0
    tracked = np.array([cx_f, cy_f], dtype=np.float64)
    return od_outer, color_frac, tracked


def _strip_sleeve_body_metrics(
    gray: np.ndarray,
    hsv: np.ndarray,
    center: np.ndarray,
    half_h: int = 42,
) -> Tuple[float, float, float]:
    """
    Column strip at the station center (not full perpendicular average).
    Distinguishes solid matte sleeve from exposed colored wires on white.
    """
    h, w = gray.shape[:2]
    cx, cy = int(round(center[0])), int(round(center[1]))
    y0b, y1b = max(0, cy - half_h), min(h, cy + half_h)
    x0b, x1b = max(0, cx), min(w, cx + 4)
    strip_g = gray[y0b:y1b, x0b:x1b]
    strip_s = hsv[y0b:y1b, x0b:x1b, 1]
    if strip_g.size < 10:
        return 255.0, 255.0, 0.0
    return (
        float(np.mean(strip_s)),
        float(strip_g.min()),
        float((strip_g < 25).mean()),
    )


def _has_exposed_wire_corridor(
    gray: np.ndarray,
    hsv: np.ndarray,
    stations: List[Tuple[float, float, float, np.ndarray]],
    min_run: int = 12,
) -> bool:
    """True when a long high-saturation gap (colored wires on white) precedes the sleeve."""
    run = 0
    max_run = 0
    for row in stations:
        sat_m, _, frac25 = _strip_sleeve_body_metrics(gray, hsv, row[3])
        if sat_m > 88.0 and frac25 < 0.12:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return max_run >= min_run


def _is_matte_sleeve_station(
    sat_m: float,
    min_g: float,
    frac25: float,
    color_frac: float,
    od_px: float,
) -> bool:
    """
    Matte RBK-3 sleeve: low saturation, low color fraction, full tube OD.
    Charcoal sleeves often have min gray 26–40 (not <22).
    """
    if color_frac >= 0.14 or od_px < 36.0:
        return False
    if sat_m < 55.0 and color_frac < 0.12:
        return True
    if sat_m < 50.0 and min_g < 45.0 and frac25 > 0.01:
        return True
    return False


def _trim_stations_to_solid_sleeve(
    img: np.ndarray,
    stations: List[Tuple[float, float, float, np.ndarray]],
    welding_splice_contour: Optional[np.ndarray],
) -> Tuple[List[Tuple[float, float, float, np.ndarray]], Dict[str, Any]]:
    """
    Drop the wire-corridor section between welding splice and heat-shrink body.
    Outer-boundary OD fires on dark gaps between blue wires; solid sleeve
    has low column saturation and a high fraction of near-black pixels.
    """
    dbg: Dict[str, Any] = {}
    if len(stations) < 4:
        return stations, dbg

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    if not _has_exposed_wire_corridor(gray, hsv, stations):
        dbg["wire_corridor"] = False
        return stations, dbg
    dbg["wire_corridor"] = True

    start_idx = 0
    for i in range(1, len(stations)):
        sat_prev, _, _ = _strip_sleeve_body_metrics(gray, hsv, stations[i - 1][3])
        sat_cur, min_g, frac25 = _strip_sleeve_body_metrics(gray, hsv, stations[i][3])
        if sat_prev > 72.0 and sat_cur < 52.0:
            start_idx = max(0, i - 1)
            dbg["sleeve_body_start_x"] = int(stations[start_idx][3][0])
            dbg["body_start_sat"] = round(sat_cur, 1)
            dbg["body_start_method"] = "sat_transition"
            break

    if start_idx == 0:
        consec = 0
        for i, row in enumerate(stations):
            color_frac, od_px = row[1], row[2]
            sat_m, min_g, frac25 = _strip_sleeve_body_metrics(gray, hsv, row[3])
            if _is_matte_sleeve_station(sat_m, min_g, frac25, color_frac, od_px):
                consec += 1
                if consec >= 2:
                    start_idx = max(0, i - 1)
                    dbg["sleeve_body_start_x"] = int(stations[start_idx][3][0])
                    dbg["body_start_sat"] = round(sat_m, 1)
                    dbg["body_start_method"] = "matte_tube"
                    break
            else:
                consec = 0

    if start_idx == 0:
        consec = 0
        for i, row in enumerate(stations):
            _, _, _, center = row
            sat_m, min_g, frac25 = _strip_sleeve_body_metrics(gray, hsv, center)
            solid = (
                sat_m < 50.0 and min_g < 22.0 and frac25 > 0.10
            ) or (sat_m < 38.0 and min_g < 25.0)
            if solid:
                consec += 1
                if consec >= 2:
                    start_idx = max(0, i - 1)
                    dbg["sleeve_body_start_x"] = int(stations[start_idx][3][0])
                    dbg["body_start_sat"] = round(sat_m, 1)
                    dbg["body_start_method"] = "solid_strip"
                    break
            else:
                consec = 0

    if start_idx == 0 and welding_splice_contour is not None:
        cx_end = cv2.boundingRect(welding_splice_contour)[0] + cv2.boundingRect(welding_splice_contour)[2]
        min_x = cx_end + 80
        for i, row in enumerate(stations):
            if row[3][0] >= min_x:
                start_idx = i
                dbg["sleeve_body_start_x"] = int(row[3][0])
                dbg["body_start_fallback"] = "past_welding_splice_gap"
                break

    trimmed = stations[start_idx:]
    dbg["stations_trimmed_head"] = start_idx

    wire_tail = 0
    for i in range(len(trimmed) - 1, -1, -1):
        color_frac, od_px = trimmed[i][1], trimmed[i][2]
        sat_m, min_g, frac25 = _strip_sleeve_body_metrics(gray, hsv, trimmed[i][3])
        wire_exit = sat_m > 82.0 or color_frac > 0.16 or (
            color_frac > 0.12 and sat_m > 65.0
        )
        if wire_exit and not _is_matte_sleeve_station(
            sat_m, min_g, frac25, color_frac, od_px
        ):
            wire_tail += 1
        else:
            break
    if wire_tail >= 3:
        keep_len = len(trimmed) - wire_tail
        if keep_len >= 3:
            dbg["stations_trimmed_tail"] = wire_tail
            dbg["sleeve_body_end_x"] = int(trimmed[keep_len - 1][3][0])
            dbg["body_end_method"] = "wire_exit"
            trimmed = trimmed[:keep_len]
        else:
            dbg["wire_tail_skipped"] = True
            dbg["wire_tail_would_remove"] = wire_tail

    return trimmed, dbg


def detect_sleeve_mask(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    welding_splice_contour: Optional[np.ndarray],
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Black heat-shrink tube to the right of the welding splice.

    Marches along the welding splice/wire axis and keeps stations where a matte black
    band (tube OD) is present; stops when colored wires dominate (tail past sleeve).
    """
    h, w = img.shape[:2]
    debug: Dict[str, Any] = {}

    if _is_likely_sleeve_only_scene(img, cable_y0, cable_y1):
        mask, solo_dbg = _detect_sleeve_mask_standalone(img, cable_y0, cable_y1)
        return mask, {**solo_dbg, "scene": "sleeve_only"}

    if welding_splice_contour is None:
        ref_mask, ref_dbg = _detect_sleeve_mask_reference_with_fallback(
            img, cable_y0, cable_y1
        )
        if ref_mask is not None:
            return ref_mask, {**ref_dbg, "scene": "assembly_unanchored"}
        mask, solo_dbg = _detect_sleeve_mask_standalone(img, cable_y0, cable_y1)
        return mask, {**solo_dbg, "scene": "assembly_unanchored"}

    ref = load_sleeve_reference()
    if ref is not None and not _welding_splice_anchor_reliable(img, welding_splice_contour):
        ref_mask, ref_dbg = _detect_sleeve_mask_reference_with_fallback(
            img, cable_y0, cable_y1, welding_splice_contour
        )
        if ref_mask is not None:
            return ref_mask, {
                **ref_dbg,
                "scene": "assembly_reference",
                "welding_splice_anchor": "unreliable",
            }

    origin, axis, perp = _welding_splice_axis(welding_splice_contour)
    x0, y0, cw, ch = cv2.boundingRect(welding_splice_contour)
    start_s = float(max(cw, ch) * 0.35)
    max_travel = min(w * 1.5, 950.0)
    debug["axis_angle_deg"] = float(np.degrees(np.arctan2(axis[1], axis[0])))

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    colored = cv2.inRange(hsv, np.array([25, 45, 40]), np.array([130, 255, 255]))
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    DARK_THR = 100
    half_len = 90.0

    # profile rows: (s, color_frac, od_outer, tracked_center)
    profile: List[Tuple[float, float, float, np.ndarray]] = []
    step = 2.0
    track_center = origin + axis * start_s
    s = start_s
    while s < max_travel:
        nominal = origin + axis * s
        # blend nominal axis with previous track so we don't overshoot on gaps
        if len(profile) > 0:
            track_center = 0.35 * nominal + 0.65 * profile[-1][3]
        else:
            track_center = nominal.copy()

        cx, cy = int(round(track_center[0])), int(round(track_center[1]))
        if cx < 0 or cx >= w or cy < 0 or cy >= h:
            break

        od_outer, color_frac, tracked = _sleeve_station_sample(
            gray_img, colored, track_center, perp, half_len, DARK_THR
        )
        profile.append((s, color_frac, od_outer, tracked))
        s += step

    runs: List[List[Tuple[float, float, float, np.ndarray]]] = []
    current: List[Tuple[float, float, float, np.ndarray]] = []
    gap_limit = step * 10
    for row in profile:
        s_val, color_frac, od_px, _ = row
        is_tube = od_px >= 18.0 and color_frac < 0.75
        if is_tube:
            if current and s_val - current[-1][0] > gap_limit:
                runs.append(current)
                current = []
            current.append(row)
        else:
            if current:
                runs.append(current)
                current = []
    if current:
        runs.append(current)

    if not runs:
        ref_mask, ref_dbg = _detect_sleeve_mask_reference_with_fallback(
            img, cable_y0, cable_y1, welding_splice_contour
        )
        if ref_mask is not None:
            return ref_mask, {**debug, **ref_dbg, "method": "reference_fallback"}
        fallback, fb_dbg = _detect_sleeve_mask_blob(img, cable_y0, cable_y1, welding_splice_contour)
        debug.update(fb_dbg)
        if fallback is not None:
            return fallback, {**debug, "method": "blob_fallback"}
        return None, {**debug, "reason": "no_tube_runs_along_axis", "profile_len": len(profile)}

    def run_score(run: List[Tuple[float, float, float, np.ndarray]]) -> float:
        span = run[-1][0] - run[0][0]
        ods = [r[2] for r in run]
        return span * float(np.median(ods))

    best_run = max(runs, key=run_score)
    if len(best_run) < 3:
        fallback, fb_dbg = _detect_sleeve_mask_blob(img, cable_y0, cable_y1, welding_splice_contour)
        debug.update(fb_dbg)
        if fallback is not None:
            return fallback, {**debug, "method": "blob_fallback", "runs": len(runs)}
        return None, {
            **debug,
            "reason": "best_tube_run_too_short",
            "runs": len(runs),
            "best_len": len(best_run),
        }

    ods = np.array([r[2] for r in best_run])
    od_thr = max(12.0, float(np.percentile(ods, 25)) * 0.55)
    valid = [r for r in best_run if r[2] >= od_thr]
    if len(valid) < 5:
        valid = best_run

    # extend forward/backward along tracked centers while tube is still visible
    def _extend_run(
        rows: List[Tuple[float, float, float, np.ndarray]],
        direction: int,
    ) -> List[Tuple[float, float, float, np.ndarray]]:
        out = list(rows)
        if not out:
            return out
        s_cur = out[-1][0] if direction > 0 else out[0][0]
        center = out[-1][3] if direction > 0 else out[0][3]
        misses = 0
        while misses < 4 and 0 <= s_cur < max_travel:
            s_cur += step * direction
            nominal = origin + axis * s_cur
            blend = 0.25 * nominal + 0.75 * center
            od_outer, color_frac, tracked = _sleeve_station_sample(
                gray_img, colored, blend, perp, half_len, DARK_THR
            )
            if od_outer >= 15.0 and color_frac < 0.78:
                row = (s_cur, color_frac, od_outer, tracked)
                if direction > 0:
                    out.append(row)
                else:
                    out.insert(0, row)
                center = tracked
                misses = 0
            else:
                misses += 1
        return out

    valid = _extend_run(valid, -1)
    valid = _extend_run(valid, 1)

    valid, trim_dbg = _trim_stations_to_solid_sleeve(img, valid, welding_splice_contour)
    debug.update(trim_dbg)
    if len(valid) < 3:
        ref_mask, ref_dbg = _detect_sleeve_mask_reference_with_fallback(
            img, cable_y0, cable_y1, welding_splice_contour
        )
        if ref_mask is not None:
            return ref_mask, {**debug, **ref_dbg, "method": "reference_fallback"}
        fallback, fb_dbg = _detect_sleeve_mask_blob(img, cable_y0, cable_y1, welding_splice_contour)
        debug.update(fb_dbg)
        if fallback is not None:
            return fallback, {**debug, "method": "blob_fallback"}
        return None, {**debug, "reason": "sleeve_body_trim_too_short"}

    stations = valid
    s_start = stations[0][0]
    s_end = stations[-1][0]
    debug["axis_span_s"] = [float(s_start), float(s_end)]
    debug["stations_used"] = len(stations)
    debug["tube_runs"] = len(runs)
    debug["center_track"] = True

    mask = np.zeros((h, w), np.uint8)
    sleeve_gray_cap = 92
    med_od = float(np.median(ods)) if len(ods) else 40.0
    half_od_cap = med_od * 0.55 + 6.0
    for _, _, od_px, center in stations:
        half_od = min(od_px / 2 + 4, half_od_cap)
        for t in np.linspace(-half_od, half_od, max(10, int(od_px) + 6)):
            pt = center + perp * t
            px2, py2 = int(round(pt[0])), int(round(pt[1]))
            if 0 <= px2 < w and 0 <= py2 < h:
                if gray_img[py2, px2] < sleeve_gray_cap:
                    mask[py2, px2] = 255

    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)), iterations=2
    )
    contour = _largest_contour(mask, 300)
    if contour is None:
        return None, {**debug, "reason": "mask_empty_after_sweep"}

    x, y, bw, bh = cv2.boundingRect(contour)
    debug["aabb"] = [int(x), int(y), int(bw), int(bh)]
    if _sleeve_mask_needs_reference_shape(contour, img):
        ref_mask, ref_dbg = _detect_sleeve_mask_reference_with_fallback(
            img, cable_y0, cable_y1, welding_splice_contour
        )
        if ref_mask is not None:
            return ref_mask, {**debug, **ref_dbg, "method": "reference_shape_fix"}
    return mask, debug


def _trim_sleeve_contour_to_body(
    contour: np.ndarray,
    px_per_mm: float,
    max_length_mm: float = 120.0,
) -> np.ndarray:
    """Keep the section of the contour with full tube OD (drop tail / taper)."""
    pts = contour.reshape(-1, 2).astype(np.float64)
    mean = pts.mean(axis=0)
    centered = pts - mean
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    major = vh[0]
    if major[0] < 0:
        major = -major
    minor = np.array([-major[1], major[0]])
    along = centered @ major
    perp = centered @ minor

    step = 4.0
    stations: List[Tuple[float, float]] = []
    s = float(along.min())
    while s <= float(along.max()):
        sel = (along >= s - step) & (along < s + step)
        if sel.sum() >= 2:
            stations.append((s, float(np.ptp(perp[sel]))))
        s += step

    if len(stations) < 3:
        return contour

    peak = max(o for _, o in stations)
    good = [a for a, o in stations if o >= peak * 0.40]
    if len(good) < 2:
        return contour

    a0, a1 = min(good), max(good)
    keep = (along >= a0 - step) & (along <= a1 + step)
    trimmed = pts[keep]
    if len(trimmed) < 4:
        return contour

    span_mm = (a1 - a0) / px_per_mm
    if span_mm > max_length_mm:
        mid = (a0 + a1) / 2
        half = max_length_mm * px_per_mm / 2
        keep = (along >= mid - half) & (along <= mid + half)
        trimmed = pts[keep]

    return trimmed.reshape(-1, 1, 2).astype(np.int32)


def _validate_sleeve_contour(
    img: np.ndarray,
    contour: np.ndarray,
    welding_splice_contour: Optional[np.ndarray],
    px_per_mm: float,
) -> Tuple[bool, Dict[str, Any]]:
    """
  Reject wire bundles, shadows, and wrong-row blobs that are not matte heat-shrink tube.
    """
    dbg: Dict[str, Any] = {}
    area = cv2.contourArea(contour)
    if area < 400:
        return False, {**dbg, "reject": "area_too_small", "area": float(area)}

    meas = _pca_measurement(contour, px_per_mm)
    dbg["length_mm"] = round(meas.length_mm, 2)
    dbg["height_mm"] = round(meas.height_mm, 2)

    min_od_mm = 3.5
    min_len_mm = 8.0
    if meas.height_mm < min_od_mm:
        return False, {**dbg, "reject": "od_below_min", "min_od_mm": min_od_mm}
    if meas.length_mm < min_len_mm:
        return False, {**dbg, "reject": "length_below_min", "min_len_mm": min_len_mm}
    if meas.length_px > 0 and meas.height_px / meas.length_px > 0.82:
        return False, {**dbg, "reject": "not_elongated_enough"}

    _, (rw, rh), _ = cv2.minAreaRect(contour)
    fill = area / max(float(rw * rh), 1.0)
    dbg["fill_ratio"] = round(fill, 3)
    if fill < 0.32:
        return False, {**dbg, "reject": "low_fill_ratio"}

    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s_vals = hsv[:, :, 1][mask > 0]
    if s_vals.size < 20:
        return False, {**dbg, "reject": "mask_too_sparse"}
    dbg["sat_mean"] = round(float(np.mean(s_vals)), 1)
    dbg["sat_median"] = round(float(np.median(s_vals)), 1)
    low_sat_frac = float((s_vals < 50).mean())
    dbg["low_sat_frac"] = round(low_sat_frac, 3)
    if low_sat_frac < 0.12:
        return False, {**dbg, "reject": "insufficient_matte_black"}
    if dbg["sat_mean"] > 95 and low_sat_frac < 0.20:
        return False, {**dbg, "reject": "high_saturation"}

    colored = cv2.inRange(hsv, np.array([25, 45, 40]), np.array([130, 255, 255]))
    colored_px = int(cv2.countNonZero(cv2.bitwise_and(colored, mask)))
    colored_frac = colored_px / max(area, 1)
    dbg["colored_frac"] = round(colored_frac, 3)
    if colored_frac > 0.52:
        return False, {**dbg, "reject": "colored_pixels_in_mask"}

    if welding_splice_contour is not None:
        welding_splice_mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(welding_splice_mask, [welding_splice_contour], -1, 255, -1)
        overlap_px = int(cv2.countNonZero(cv2.bitwise_and(mask, welding_splice_mask)))
        overlap_frac = overlap_px / max(area, 1)
        dbg["welding_splice_overlap_frac"] = round(overlap_frac, 3)
        if overlap_frac > 0.30:
            return False, {**dbg, "reject": "overlaps_welding_splice"}

        cc = welding_splice_contour.reshape(-1, 2).mean(axis=0)
        sc = contour.reshape(-1, 2).mean(axis=0)
        dy = abs(float(sc[1] - cc[1]))
        _, _, _, ch = cv2.boundingRect(welding_splice_contour)
        _, _, _, bh = cv2.boundingRect(contour)
        y_tol = max(ch, bh) * 1.8 + 20
        dbg["y_offset_px"] = round(dy, 1)
        dbg["y_tol_px"] = round(y_tol, 1)
        if dy > y_tol:
            return False, {**dbg, "reject": "wrong_row_vs_welding_splice"}

        x_c, _, _, _ = cv2.boundingRect(contour)
        if x_c < float(cc[0]) - 80:
            return False, {**dbg, "reject": "sleeve_left_of_welding_splice"}

        welding_splice_meas = _pca_measurement(welding_splice_contour, px_per_mm)
        angle_diff = abs(meas.axis_angle_deg - welding_splice_meas.axis_angle_deg)
        angle_diff = min(angle_diff, 180 - angle_diff)
        dbg["angle_diff_deg"] = round(angle_diff, 1)
        if angle_diff > 30:
            return False, {**dbg, "reject": "axis_misaligned"}

    dbg["validated"] = True
    return True, dbg


def _sanity_check(name: str, m: AxisMeasurement) -> List[str]:
    warnings: List[str] = []
    if name == "welding_splice":
        if m.length_mm < 5 or m.length_mm > 40:
            warnings.append(f"welding splice length {m.length_mm:.1f} mm outside typical 5–40 mm")
        if m.height_mm < 1.5 or m.height_mm > 18:
            warnings.append(f"welding splice height {m.height_mm:.1f} mm outside typical 2–15 mm")
        if m.length_px > 0 and m.height_px / m.length_px > 0.85:
            warnings.append("welding splice axis may be wrong (height ≈ length)")
    elif name == "heat_shrink_sleeve":
        if m.length_mm < 10 or m.length_mm > 130:
            warnings.append(f"sleeve length {m.length_mm:.1f} mm outside typical 10–130 mm")
        if m.height_mm < 3 or m.height_mm > 20:
            warnings.append(f"sleeve height {m.height_mm:.1f} mm outside typical 3–20 mm")
        if m.length_px > 0 and m.height_px / m.length_px > 0.75:
            warnings.append("sleeve axis may be wrong (height ≈ length)")
    return warnings


def _detect_welding_splice_contour(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    px_per_mm: float,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], ComponentResult, List[str]]:
    """Return (contour, mask, component_result, warnings)."""
    h, w = img.shape[:2]
    warnings: List[str] = []
    if _is_likely_sleeve_only_scene(img, cable_y0, cable_y1):
        welding_splice_mask, welding_splice_dbg = None, {"reason": "sleeve_only_scene", "skipped": True}
    else:
        welding_splice_mask, welding_splice_dbg = detect_welding_splice_mask(img, cable_y0, cable_y1)
    welding_splice_contour = _largest_contour(welding_splice_mask, 300) if welding_splice_mask is not None else None

    if welding_splice_contour is not None:
        x, y, bw, bh = cv2.boundingRect(welding_splice_contour)
        area = cv2.contourArea(welding_splice_contour)
        elong = max(bw, bh) / max(1, min(bw, bh))
        barrel_ok = _refined_welding_splice_barrel_ok(welding_splice_contour) or (
            elong >= 1.15 and max(bw, bh) >= 38 and min(bw, bh) >= 10
        )
        if area < 280 or not barrel_ok:
            welding_splice_contour = None
            welding_splice_mask = None
            welding_splice_dbg["reject"] = "welding_splice_too_small_or_vertical"
        else:
            meas = _pca_measurement_welding_splice(welding_splice_contour, img, px_per_mm)
            welding_splice_mask = _contour_to_mask(welding_splice_contour, h, w)
            comp = ComponentResult(
                name="welding_splice",
                found=True,
                measurement=meas,
                contour=welding_splice_contour,
                aabb=(x, y, bw, bh),
                debug={**welding_splice_dbg, "measurement": "welding_splice_robust_pca"},
            )
            warnings.extend(_sanity_check("welding_splice", meas))
            return welding_splice_contour, welding_splice_mask, comp, warnings

    comp = ComponentResult(name="welding_splice", found=False, debug=welding_splice_dbg)
    return None, None, comp, warnings


def _detect_sleeve_component(
    img: np.ndarray,
    cable_y0: int,
    cable_y1: int,
    welding_splice_contour: Optional[np.ndarray],
    px_per_mm: float,
    welding_splice_found: bool,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], ComponentResult, List[str]]:
    """Return (contour, mask, component_result, warnings)."""
    h, w = img.shape[:2]
    warnings: List[str] = []
    sleeve_mask, sleeve_dbg = detect_sleeve_mask(
        img, cable_y0, cable_y1, welding_splice_contour
    )
    sleeve_contour = _largest_contour(sleeve_mask, 400) if sleeve_mask is not None else None

    if sleeve_contour is not None:
        if (
            sleeve_dbg.get("method") not in ("standalone", "reference")
            and not sleeve_dbg.get("center_track")
        ):
            trimmed = _trim_sleeve_contour_to_body(sleeve_contour, px_per_mm)
            if cv2.contourArea(trimmed) >= 200:
                sleeve_contour = trimmed
                sleeve_dbg["trimmed_to_tube_body"] = True
        else:
            sleeve_dbg["trimmed_to_tube_body"] = False
        ok, val_dbg = _validate_sleeve_contour(
            img, sleeve_contour, welding_splice_contour, px_per_mm
        )
        sleeve_dbg.update(val_dbg)
        if not ok:
            reject = val_dbg.get("reject", "validation")
            retry_mask, retry_dbg = _detect_sleeve_mask_reference_with_fallback(
                img, cable_y0, cable_y1, welding_splice_contour
            )
            if retry_mask is not None:
                retry_contour = _largest_contour(retry_mask, 400)
                if retry_contour is not None:
                    ok2, val2 = _validate_sleeve_contour(
                        img, retry_contour, welding_splice_contour, px_per_mm
                    )
                    if ok2:
                        sleeve_contour = retry_contour
                        sleeve_mask = retry_mask
                        sleeve_dbg = {**retry_dbg, **val2, "method": "reference_validation_retry"}
                        ok = True
            if not ok:
                comp = ComponentResult(
                    name="heat_shrink_sleeve",
                    found=False,
                    debug=sleeve_dbg,
                )
                if not welding_splice_found:
                    warnings.append(f"heat-shrink sleeve rejected ({reject})")
                return None, None, comp, warnings

        meas = _sleeve_axis_measurement(sleeve_contour, px_per_mm, sleeve_dbg)
        x, y, bw, bh = cv2.boundingRect(sleeve_contour)
        sleeve_mask = _contour_to_mask(sleeve_contour, h, w)
        comp = ComponentResult(
            name="heat_shrink_sleeve",
            found=True,
            measurement=meas,
            contour=sleeve_contour,
            aabb=(x, y, bw, bh),
            debug=sleeve_dbg,
        )
        warnings.extend(_sanity_check("heat_shrink_sleeve", meas))
        return sleeve_contour, sleeve_mask, comp, warnings

    comp = ComponentResult(name="heat_shrink_sleeve", found=False, debug=sleeve_dbg)
    if not welding_splice_found:
        warnings.append("heat-shrink sleeve not detected")
    return None, None, comp, warnings


def measure_welding_splice(
    img: np.ndarray,
    image_path: str,
    px_per_mm: float,
    px_per_mm_source: str,
) -> ImageMeasurementResult:
    """Measure welding splice only."""
    h, w = img.shape[:2]
    cable_y0, cable_y1 = detect_cable_band(img)
    _, welding_splice_mask, welding_splice_comp, warnings = _detect_welding_splice_contour(
        img, cable_y0, cable_y1, px_per_mm
    )
    result = ImageMeasurementResult(
        image_path=image_path,
        image_size=(w, h),
        px_per_mm=px_per_mm,
        px_per_mm_source=px_per_mm_source,
        cable_band_y=(cable_y0, cable_y1),
        welding_splice=welding_splice_comp,
        welding_splice_mask=welding_splice_mask,
        errors=warnings,
    )
    if not welding_splice_comp.found:
        result.errors.append("welding splice not detected")
    return result


def measure_sleeve(
    img: np.ndarray,
    image_path: str,
    px_per_mm: float,
    px_per_mm_source: str,
) -> ImageMeasurementResult:
    """Measure heat-shrink sleeve (uses welding splice contour as axis anchor when present)."""
    h, w = img.shape[:2]
    cable_y0, cable_y1 = detect_cable_band(img)
    welding_splice_contour, _, welding_splice_comp, welding_splice_warnings = _detect_welding_splice_contour(
        img, cable_y0, cable_y1, px_per_mm
    )
    _, sleeve_mask, sleeve_comp, sleeve_warnings = _detect_sleeve_component(
        img, cable_y0, cable_y1, welding_splice_contour, px_per_mm, welding_splice_comp.found
    )
    errors = list(welding_splice_warnings) + list(sleeve_warnings)
    if welding_splice_comp.found and not sleeve_comp.found:
        errors.append("welding splice detected (anchor only); sleeve not found")
    elif not welding_splice_comp.found and sleeve_comp.found:
        sleeve_comp.debug["mode"] = "sleeve_only_image"
    result = ImageMeasurementResult(
        image_path=image_path,
        image_size=(w, h),
        px_per_mm=px_per_mm,
        px_per_mm_source=px_per_mm_source,
        cable_band_y=(cable_y0, cable_y1),
        welding_splice=welding_splice_comp,
        sleeve=sleeve_comp,
        welding_splice_mask=None,
        sleeve_mask=sleeve_mask,
        errors=errors,
    )
    if not welding_splice_comp.found and sleeve_comp.found:
        result.welding_splice.debug["mode"] = "sleeve_only_image"
    return result


def measure_image(
    img: np.ndarray,
    image_path: str,
    px_per_mm: float,
    px_per_mm_source: str,
) -> ImageMeasurementResult:
    h, w = img.shape[:2]
    result = ImageMeasurementResult(
        image_path=image_path,
        image_size=(w, h),
        px_per_mm=px_per_mm,
        px_per_mm_source=px_per_mm_source,
    )

    cable_y0, cable_y1 = detect_cable_band(img)
    result.cable_band_y = (cable_y0, cable_y1)

    welding_splice_contour, welding_splice_mask, welding_splice_comp, welding_splice_warnings = _detect_welding_splice_contour(
        img, cable_y0, cable_y1, px_per_mm
    )
    result.welding_splice = welding_splice_comp
    result.welding_splice_mask = welding_splice_mask
    result.errors.extend(welding_splice_warnings)

    _, sleeve_mask, sleeve_comp, sleeve_warnings = _detect_sleeve_component(
        img, cable_y0, cable_y1, welding_splice_contour, px_per_mm, welding_splice_comp.found
    )
    result.sleeve = sleeve_comp
    result.sleeve_mask = sleeve_mask
    result.errors.extend(sleeve_warnings)

    if not welding_splice_comp.found and not sleeve_comp.found:
        result.errors.append("welding splice not detected")
    elif not welding_splice_comp.found and sleeve_comp.found:
        result.welding_splice = ComponentResult(
            name="welding_splice",
            found=False,
            debug={"mode": "sleeve_only_image", "skipped": True},
        )
    elif welding_splice_comp.found and not sleeve_comp.found:
        if result.welding_splice:
            result.welding_splice.debug["mode"] = "welding_splice_only_image"
        result.errors.append("welding splice detected (anchor only); sleeve not found")

    return result


def _contour_to_mask(contour: np.ndarray, height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    return mask


def build_combined_mask(
    result: ImageMeasurementResult,
) -> np.ndarray:
    """BGR mask: welding splice=orange, heat-shrink sleeve=magenta, overlap=white."""
    h, w = result.image_size[1], result.image_size[0]
    combined = np.zeros((h, w, 3), dtype=np.uint8)
    if result.welding_splice_mask is not None:
        combined[result.welding_splice_mask > 0] = _COLOR_WELDING_SPLICE
    if result.sleeve_mask is not None:
        sel = result.sleeve_mask > 0
        combined[sel] = _COLOR_SLEEVE
    if result.welding_splice_mask is not None and result.sleeve_mask is not None:
        overlap = cv2.bitwise_and(result.welding_splice_mask, result.sleeve_mask)
        combined[overlap > 0] = (255, 255, 255)
    return combined


def render_mask_overlay(
    img: np.ndarray,
    result: ImageMeasurementResult,
    alpha: float = 0.45,
) -> np.ndarray:
    """Semi-transparent component masks on the capture (for tuning detection)."""
    out = img.copy()
    overlay = out.copy()
    if result.welding_splice_mask is not None:
        overlay[result.welding_splice_mask > 0] = _COLOR_WELDING_SPLICE
    if result.sleeve_mask is not None:
        overlay[result.sleeve_mask > 0] = _COLOR_SLEEVE
    return cv2.addWeighted(overlay, alpha, out, 1.0 - alpha, 0)


def save_measurement_artifacts(
    img: np.ndarray,
    result: ImageMeasurementResult,
    out_dir: Path,
    stem: str = "capture",
) -> Dict[str, str]:
    """
    Write masks and annotated images for detection review / tuning.

    Returns map of artifact name → file path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, str] = {}

    raw_p = out_dir / f"{stem}.png"
    cv2.imwrite(str(raw_p), img)
    written["capture"] = str(raw_p)

    measured = annotate_image(img, result)
    ann_p = out_dir / f"{stem}_measured.png"
    cv2.imwrite(str(ann_p), measured)
    written["measured"] = str(ann_p)

    overlay = render_mask_overlay(img, result)
    ovl_p = out_dir / f"{stem}_mask_overlay.png"
    cv2.imwrite(str(ovl_p), overlay)
    written["mask_overlay"] = str(ovl_p)

    combined = build_combined_mask(result)
    comb_p = out_dir / f"{stem}_mask_combined.png"
    cv2.imwrite(str(comb_p), combined)
    written["mask_combined"] = str(comb_p)

    if result.welding_splice_mask is not None:
        p = out_dir / f"{stem}_mask_welding_splice.png"
        cv2.imwrite(str(p), result.welding_splice_mask)
        written["mask_welding_splice"] = str(p)

    sleeve_p = out_dir / f"{stem}_mask_sleeve.png"
    if result.sleeve_mask is not None:
        cv2.imwrite(str(sleeve_p), result.sleeve_mask)
        written["mask_sleeve"] = str(sleeve_p)
    elif sleeve_p.is_file():
        sleeve_p.unlink()

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": written,
        "measurement": result.to_dict(),
    }
    json_p = out_dir / f"{stem}_measurement.json"
    with open(json_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    written["measurement_json"] = str(json_p)

    return written


def save_welding_splice_artifacts(
    img: np.ndarray,
    result: ImageMeasurementResult,
    out_dir: Path,
    stem: str = "capture",
) -> Dict[str, str]:
    """Write welding-splice-only masks and annotated images."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, str] = {}

    raw_p = out_dir / f"{stem}.png"
    cv2.imwrite(str(raw_p), img)
    written["capture"] = str(raw_p)

    display = ImageMeasurementResult(
        image_path=result.image_path,
        image_size=result.image_size,
        px_per_mm=result.px_per_mm,
        px_per_mm_source=result.px_per_mm_source,
        cable_band_y=result.cable_band_y,
        welding_splice=result.welding_splice,
        welding_splice_mask=result.welding_splice_mask,
    )
    measured = annotate_image(img, display)
    ann_p = out_dir / f"{stem}_measured.png"
    cv2.imwrite(str(ann_p), measured)
    written["measured"] = str(ann_p)

    if result.welding_splice_mask is not None:
        overlay = img.copy()
        blend = overlay.copy()
        blend[result.welding_splice_mask > 0] = _COLOR_WELDING_SPLICE
        ovl = cv2.addWeighted(blend, 0.45, overlay, 0.55, 0)
        ovl_p = out_dir / f"{stem}_mask_overlay.png"
        cv2.imwrite(str(ovl_p), ovl)
        written["mask_overlay"] = str(ovl_p)

        mask_p = out_dir / f"{stem}_mask_welding_splice.png"
        cv2.imwrite(str(mask_p), result.welding_splice_mask)
        written["mask_welding_splice"] = str(mask_p)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "component": "welding_splice",
        "artifacts": written,
        "measurement": result.to_dict(),
    }
    json_p = out_dir / f"{stem}_measurement.json"
    with open(json_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    written["measurement_json"] = str(json_p)
    return written


def save_sleeve_artifacts(
    img: np.ndarray,
    result: ImageMeasurementResult,
    out_dir: Path,
    stem: str = "capture",
) -> Dict[str, str]:
    """Write sleeve-only masks and annotated images."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: Dict[str, str] = {}

    raw_p = out_dir / f"{stem}.png"
    cv2.imwrite(str(raw_p), img)
    written["capture"] = str(raw_p)

    display = ImageMeasurementResult(
        image_path=result.image_path,
        image_size=result.image_size,
        px_per_mm=result.px_per_mm,
        px_per_mm_source=result.px_per_mm_source,
        cable_band_y=result.cable_band_y,
        sleeve=result.sleeve,
        sleeve_mask=result.sleeve_mask,
    )
    measured = annotate_image(img, display)
    ann_p = out_dir / f"{stem}_measured.png"
    cv2.imwrite(str(ann_p), measured)
    written["measured"] = str(ann_p)

    if result.sleeve_mask is not None:
        overlay = img.copy()
        blend = overlay.copy()
        blend[result.sleeve_mask > 0] = _COLOR_SLEEVE
        ovl = cv2.addWeighted(blend, 0.45, overlay, 0.55, 0)
        ovl_p = out_dir / f"{stem}_mask_overlay.png"
        cv2.imwrite(str(ovl_p), ovl)
        written["mask_overlay"] = str(ovl_p)

        mask_p = out_dir / f"{stem}_mask_sleeve.png"
        cv2.imwrite(str(mask_p), result.sleeve_mask)
        written["mask_sleeve"] = str(mask_p)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "component": "heat_shrink_sleeve",
        "artifacts": written,
        "measurement": result.to_dict(),
    }
    json_p = out_dir / f"{stem}_measurement.json"
    with open(json_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    written["measurement_json"] = str(json_p)
    return written


def print_welding_splice_result(result: ImageMeasurementResult) -> None:
    print(f"\n{result.image_path}")
    print(f"  scale: {result.px_per_mm:.3f} px/mm ({result.px_per_mm_source})")
    if result.cable_band_y:
        print(f"  cable band y: {result.cable_band_y[0]}–{result.cable_band_y[1]}")
    if result.welding_splice:
        comp = result.welding_splice
        if comp.found and comp.measurement:
            m = comp.measurement
            print(
                f"  Welding splice: L={m.length_mm:.2f} mm  H={m.height_mm:.2f} mm  "
                f"(angle {m.axis_angle_deg:.1f}°)"
            )
        else:
            print(f"  Welding splice: NOT FOUND — {comp.debug.get('reason', comp.debug)}")
    for err in result.errors:
        print(f"  warning: {err}")


def print_sleeve_result(result: ImageMeasurementResult) -> None:
    print(f"\n{result.image_path}")
    print(f"  scale: {result.px_per_mm:.3f} px/mm ({result.px_per_mm_source})")
    if result.cable_band_y:
        print(f"  cable band y: {result.cable_band_y[0]}–{result.cable_band_y[1]}")
    if result.welding_splice and not result.welding_splice.found:
        print(f"  welding splice anchor: not found ({result.welding_splice.debug.get('reason', 'n/a')})")
    elif result.welding_splice and result.welding_splice.found:
        print("  welding splice anchor: found (axis reference)")
    if result.sleeve:
        comp = result.sleeve
        if comp.found and comp.measurement:
            m = comp.measurement
            print(
                f"  Heat-shrink sleeve: L={m.length_mm:.2f} mm  H={m.height_mm:.2f} mm  "
                f"(angle {m.axis_angle_deg:.1f}°)"
            )
        else:
            print(
                f"  Heat-shrink sleeve: NOT FOUND — "
                f"{comp.debug.get('reason', comp.debug)}"
            )
    for err in result.errors:
        print(f"  warning: {err}")


def _draw_axis_lines(
    out: np.ndarray,
    comp: ComponentResult,
    length_color: Tuple[int, int, int],
    label: str,
) -> None:
    if not comp.found or comp.measurement is None or comp.contour is None:
        return
    m = comp.measurement
    mean = np.array(m.centroid)
    major = np.array(m.major_unit)
    minor = np.array(m.minor_unit)
    L2 = m.length_px / 2
    H2 = m.height_px / 2
    p1 = mean - major * L2
    p2 = mean + major * L2
    q1 = mean - minor * H2
    q2 = mean + minor * H2
    cv2.line(out, tuple(p1.astype(int)), tuple(p2.astype(int)), length_color, 3)
    cv2.line(out, tuple(q1.astype(int)), tuple(q2.astype(int)), _COLOR_HEIGHT, 2)
    cv2.drawContours(out, [comp.contour], 0, length_color, 2)

    # Place L/H labels on the upper part of the component (above top edge, centered).
    _, y0, _, bh = cv2.boundingRect(comp.contour)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.55
    thickness = 2
    l_text = f"{label} L={m.length_mm:.1f}mm"
    h_text = f"H={m.height_mm:.1f}mm"
    (l_w, l_h), _ = cv2.getTextSize(l_text, font, scale, thickness)
    (h_w, h_h), _ = cv2.getTextSize(h_text, font, scale, thickness)
    anchor_x = int(mean[0])
    gap = 4
    block_h = l_h + h_h + gap
    y_top = int(y0) - block_h - 6
    if y_top < 4:
        y_top = int(y0) + max(4, int(bh * 0.12))
    h_img, w_img = out.shape[:2]

    def _clamp_pos(x: int, y: int, tw: int) -> Tuple[int, int]:
        x = max(4, min(x, w_img - tw - 4))
        y = max(l_h + 2, min(y, h_img - 4))
        return x, y

    l_pos = _clamp_pos(anchor_x - l_w // 2, y_top + l_h, l_w)
    h_pos = _clamp_pos(anchor_x - h_w // 2, y_top + l_h + gap + h_h, h_w)
    cv2.putText(out, l_text, l_pos, font, scale, length_color, thickness)
    cv2.putText(out, h_text, h_pos, font, scale, _COLOR_HEIGHT, thickness)


def annotate_image(img: np.ndarray, result: ImageMeasurementResult) -> np.ndarray:
    out = img.copy()
    if result.cable_band_y:
        y0, y1 = result.cable_band_y
        cv2.rectangle(out, (0, y0), (out.shape[1] - 1, y1), (180, 180, 180), 1)

    if result.welding_splice:
        _draw_axis_lines(out, result.welding_splice, _COLOR_WELDING_SPLICE, "Welding splice")
    if result.sleeve:
        _draw_axis_lines(out, result.sleeve, _COLOR_SLEEVE, "Heat-shrink sleeve")

    cv2.putText(
        out,
        "L = length along axis (welding splice / heat-shrink sleeve)",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        _COLOR_LENGTH,
        1,
    )
    cv2.putText(
        out,
        "H = cross-section / OD (cyan)",
        (12, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        _COLOR_HEIGHT,
        1,
    )
    cv2.putText(
        out,
        f"scale {result.px_per_mm:.2f} px/mm",
        (12, 72),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (80, 80, 80),
        1,
    )
    return out


def print_result(result: ImageMeasurementResult) -> None:
    """Console summary for welding splice + sleeve (same style as print_sleeve_result)."""
    print(f"\n{result.image_path}")
    print(f"  scale: {result.px_per_mm:.3f} px/mm ({result.px_per_mm_source})")
    if result.cable_band_y:
        print(f"  cable band y: {result.cable_band_y[0]}–{result.cable_band_y[1]}")
    if result.welding_splice:
        comp = result.welding_splice
        if comp.found and comp.measurement:
            m = comp.measurement
            print(
                f"  Welding splice: L={m.length_mm:.2f} mm  H={m.height_mm:.2f} mm  "
                f"(angle {m.axis_angle_deg:.1f}°)"
            )
        elif comp.debug.get("mode") not in ("sleeve_only_image", "sleeve_only_scene"):
            print(
                f"  Welding splice: NOT FOUND — "
                f"{comp.debug.get('reason', comp.debug)}"
            )
    if result.sleeve:
        if result.welding_splice and not result.welding_splice.found:
            if result.welding_splice.debug.get("mode") != "sleeve_only_image":
                print(
                    f"  welding splice anchor: not found "
                    f"({result.welding_splice.debug.get('reason', 'n/a')})"
                )
        elif result.welding_splice and result.welding_splice.found:
            print("  welding splice anchor: found (axis reference)")
        comp = result.sleeve
        if comp.found and comp.measurement:
            m = comp.measurement
            print(
                f"  Heat-shrink sleeve: L={m.length_mm:.2f} mm  H={m.height_mm:.2f} mm  "
                f"(angle {m.axis_angle_deg:.1f}°)"
            )
        else:
            print(
                f"  Heat-shrink sleeve: NOT FOUND — "
                f"{comp.debug.get('reject', comp.debug.get('reason', comp.debug))}"
            )
    for err in result.errors:
        print(f"  warning: {err}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure welding splice and heat-shrink sleeve (L along axis, H cross-section).",
    )
    parser.add_argument(
        "images",
        nargs="+",
        type=Path,
        help="Image path(s) or glob (quote glob in shell)",
    )
    parser.add_argument(
        "--calibration",
        "-c",
        type=Path,
        help="Calibration session folder containing calibration.json",
    )
    parser.add_argument(
        "--px-per-mm",
        type=float,
        help="Override scale (default: calibration.json or 5.1)",
    )
    parser.add_argument(
        "--annotate-out",
        type=Path,
        help="Write annotated image (single input only)",
    )
    parser.add_argument(
        "--annotate-dir",
        type=Path,
        help="Write annotated copies for each input into this directory",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Write all results as JSON",
    )
    parser.add_argument(
        "--mask-dir",
        type=Path,
        help="Save welding splice / heat-shrink sleeve masks, overlay, measured image, and JSON per input",
    )
    args = parser.parse_args(argv)

    # expand globs
    paths: List[Path] = []
    for p in args.images:
        if "*" in str(p):
            paths.extend(sorted(Path().glob(str(p))))
        else:
            paths.append(p)
    paths = [p.resolve() for p in paths if p.is_file()]
    if not paths:
        print("No image files found.", file=sys.stderr)
        return 1

    cal_dir = args.calibration.resolve() if args.calibration else None
    px_per_mm, px_src = load_px_per_mm(cal_dir, args.px_per_mm)

    all_results: List[Dict[str, Any]] = []
    for path in paths:
        img = cv2.imread(str(path))
        if img is None:
            print(f"Could not read {path}", file=sys.stderr)
            continue
        result = measure_image(img, str(path), px_per_mm, px_src)
        print_result(result)
        all_results.append(result.to_dict())

        if args.annotate_out and len(paths) == 1:
            ann = annotate_image(img, result)
            args.annotate_out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(args.annotate_out), ann)
            print(f"  annotated: {args.annotate_out}")

        if args.annotate_dir:
            args.annotate_dir.mkdir(parents=True, exist_ok=True)
            ann = annotate_image(img, result)
            out_p = args.annotate_dir / f"{path.stem}_measured.png"
            cv2.imwrite(str(out_p), ann)
            print(f"  annotated: {out_p}")

        if args.mask_dir:
            sub = args.mask_dir / path.stem
            files = save_measurement_artifacts(img, result, sub, stem=path.stem)
            print(f"  masks: {sub}")
            for key, fp in files.items():
                if key.endswith(".json"):
                    continue
                print(f"    {key}: {fp}")

    if args.json_out:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "px_per_mm": px_per_mm,
            "px_per_mm_source": px_src,
            "definitions": {
                "length_mm": "along component main axis",
                "height_mm": "cross-section perpendicular to axis",
            },
            "results": all_results,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        print(f"\nJSON: {args.json_out}")

    return 0 if all_results else 1




if __name__ == "__main__":
    sys.exit(main())
