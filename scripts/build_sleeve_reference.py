#!/usr/bin/env python3
"""
Build heat-shrink sleeve reference masks and profile from golden captures.

Default sources: backend/storage/Measurement/Data/capture5.png, capture8.png
Output: backend/storage/reference/sleeve/

  sleeve_reference.json
  capture5_mask.png, capture8_mask.png
  capture5_source.png, capture8_source.png (copies)

Usage:
  python3 scripts/build_sleeve_reference.py
  python3 scripts/build_sleeve_reference.py path/to/sleeve.png ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from measure_welding_splice_sleeve import (  # noqa: E402
    _contour_axis_frame,
    _largest_contour,
    _refine_full_tube_mask,
    _tube_edges_along_perp,
)

_REPO = _SCRIPT_DIR.parent
_DEFAULT_SOURCES = [
    _REPO / "backend/storage/Measurement/Data/capture5.png",
    _REPO / "backend/storage/Measurement/Data/capture8.png",
]
_REF_DIR = _REPO / "backend/storage/reference/sleeve"


def _seed_mask(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    colored = cv2.inRange(
        hsv,
        np.array([20, 55, 40], dtype=np.uint8),
        np.array([135, 255, 255], dtype=np.uint8),
    )
    seed = ((gray < 95) & (hsv[:, :, 1] < 70) & (colored == 0)).astype(np.uint8) * 255
    seed = cv2.morphologyEx(
        seed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    )
    seed = cv2.morphologyEx(
        seed, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5))
    )
    return seed


def _edge_profile_from_contour(
    img: np.ndarray,
    contour: np.ndarray,
    gray_cap: int = 96,
) -> Dict[str, Any]:
    """Per-axis edge half-widths from a golden full-tube mask (capture5/8)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mean, major, perp, s_min, s_max = _contour_axis_frame(contour)
    span = max(s_max - s_min, 1.0)
    half_widths: List[float] = []
    step = 3.0
    s = s_min
    while s <= s_max + step * 0.5:
        center = mean + major * s
        edges = _tube_edges_along_perp(
            gray, hsv, center, perp, 80.0, float(gray_cap), None
        )
        if edges is not None:
            half_widths.append((edges[1] - edges[0]) / 2.0)
        s += step
    if not half_widths:
        return {}
    arr = np.array(half_widths, dtype=np.float64)
    return {
        "half_width_median": round(float(np.median(arr)), 2),
        "half_width_p90": round(float(np.percentile(arr, 90)), 2),
        "stations": len(half_widths),
    }


def build_one(img_path: Path) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    img = cv2.imread(str(img_path))
    if img is None:
        raise FileNotFoundError(img_path)
    h, w = img.shape[:2]
    seed = _seed_mask(img)
    contour = _largest_contour(seed, 3000)
    if contour is None:
        raise RuntimeError(f"no tube seed in {img_path.name}")

    refined_mask, refined_contour, _ = _refine_full_tube_mask(img, contour)
    mask = refined_mask.copy() if refined_mask is not None else seed.copy()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask[gray >= 96] = 0
    contour = _largest_contour(mask, 800)
    if contour is None:
        raise RuntimeError(f"refined mask empty for {img_path.name}")

    out = np.zeros((h, w), np.uint8)
    cv2.drawContours(out, [contour], -1, 255, -1)

    pts = out > 0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    g = gray[pts]
    s = hsv[:, :, 1][pts]
    x, y, bw, bh = cv2.boundingRect(contour)
    area = float(cv2.contourArea(contour))
    profile: Dict[str, Any] = {
        "source": img_path.name,
        "aabb": [int(x), int(y), int(bw), int(bh)],
        "area": area,
        "elong": max(bw, bh) / max(1, min(bw, bh)),
        "fill": area / max(bw * bh, 1),
        "gray_p5": int(np.percentile(g, 5)),
        "gray_p50": int(np.percentile(g, 50)),
        "gray_p90": int(np.percentile(g, 90)),
        "gray_p95": int(np.percentile(g, 95)),
        "sat_p50": int(np.percentile(s, 50)),
        "sat_p90": int(np.percentile(s, 90)),
    }
    return img, out, profile


def build_reference(sources: List[Path], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    profiles: List[Dict[str, Any]] = []
    edge_profiles: List[Dict[str, Any]] = []
    for src in sources:
        img, mask, prof = build_one(src)
        stem = src.stem
        cv2.imwrite(str(out_dir / f"{stem}_mask.png"), mask)
        cv2.imwrite(str(out_dir / f"{stem}_source.png"), img)
        profiles.append(prof)
        c = _largest_contour(mask, 800)
        if c is not None:
            ep = _edge_profile_from_contour(img, c)
            if ep:
                ep["source"] = src.name
                edge_profiles.append(ep)
        print(f"  {src.name}: aabb={prof['aabb']} area={prof['area']:.0f}")

    edge_profile: Dict[str, Any] = {}
    if edge_profiles:
        edge_profile = {
            "half_width_median": round(
                float(np.median([e["half_width_median"] for e in edge_profiles])), 2
            ),
            "half_width_p90": round(
                float(np.median([e["half_width_p90"] for e in edge_profiles])), 2
            ),
            "sources": [e.get("source") for e in edge_profiles],
        }

    ref = {
        "version": 1,
        "sources": [p.name for p in sources],
        "description": "Matte black RBK-3 heat-shrink tube on white background.",
        "gray_max": int(max(p["gray_p95"] for p in profiles) + 8),
        "gray_seed": int(np.median([p["gray_p90"] for p in profiles])),
        "gray_p50_target": int(np.median([p["gray_p50"] for p in profiles])),
        "sat_max": int(max(p["sat_p90"] for p in profiles) + 15),
        "sat_median_target": int(np.median([p["sat_p50"] for p in profiles])),
        "gray_cap": 96,
        "elong_min": 2.8,
        "elong_max": 12.0,
        "fill_min": 0.38,
        "fill_max": 0.88,
        "min_area_px": int(min(p["area"] for p in profiles) * 0.38),
        "min_length_px": int(min(p["aabb"][2] for p in profiles) * 0.75),
        "min_height_px": 12,
        "max_height_px": int(max(p["aabb"][3] for p in profiles) * 1.35),
        "max_width_frac": 0.55,
        "morph_close_h": 25,
        "morph_close_v": 5,
        "profiles": profiles,
        "edge_profile": edge_profile,
    }
    json_path = out_dir / "sleeve_reference.json"
    json_path.write_text(json.dumps(ref, indent=2) + "\n", encoding="utf-8")
    return json_path


def main() -> int:
    sources = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else _DEFAULT_SOURCES
    for p in sources:
        if not p.is_file():
            print(f"Missing: {p}", file=sys.stderr)
            return 1
    print(f"Building sleeve reference -> {_REF_DIR}")
    json_path = build_reference(sources, _REF_DIR)
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
