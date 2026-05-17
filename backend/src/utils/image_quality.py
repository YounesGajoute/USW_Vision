"""
Multi-signal image quality analysis for master registration, capture API, and live preview.

Combines:
  - Soft clipping / roll-off (highlights and shadows), not only hard saturation counts
  - Luminance comfort score using a mean + median blend (robust to large shadows)
  - Tonal spread (p5–p95) as contrast / usable dynamic range
  - Detail: Laplacian variance + Tenengrad energy on resolution-normalized grayscale
  - Histogram entropy as a weak "information / blank frame" guard

All sub-scores are 0–100. The composite is a weighted blend with guards for nearly
uniform or extremely blurry frames.
"""

from __future__ import annotations

from typing import Any, Dict

import cv2
import numpy as np

# Longest edge for metric grayscale — keeps Laplacian / Tenengrad comparable across sensors.
_METRIC_MAX_SIDE = 960


def _ensure_rgb_u8(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("empty image")
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.ndim != 3:
        raise ValueError("expected 2d or 3d array")
    c = image.shape[2]
    if c == 1:
        return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2RGB)
    if c == 2:
        return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2RGB)
    if c >= 4:
        return image[:, :, :3].copy()
    return image


def prepare_gray_for_metrics(image: np.ndarray) -> np.ndarray:
    """RGB uint8 → grayscale uint8, resized so longest edge ≤ _METRIC_MAX_SIDE."""
    rgb = _ensure_rgb_u8(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape[:2]
    m = max(h, w)
    if m <= _METRIC_MAX_SIDE:
        return gray
    scale = _METRIC_MAX_SIDE / float(m)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    return cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_AREA)


def laplacian_variance(gray_u8: np.ndarray) -> float:
    lap = cv2.Laplacian(gray_u8.astype(np.float32), cv2.CV_32F)
    return float(lap.var())


def tenengrad_energy_mean(gray_u8: np.ndarray) -> float:
    gx = cv2.Sobel(gray_u8, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_u8, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def _histogram_entropy_index(gray_u8: np.ndarray) -> float:
    hist = cv2.calcHist([gray_u8], [0], None, [256], [0, 256]).ravel().astype(np.float64)
    s = float(hist.sum())
    if s <= 0:
        return 0.0
    p = hist / s
    p = p[p > 0]
    ent = float(-np.sum(p * np.log2(p + 1e-15)))
    return float(np.clip(ent / 8.0 * 100.0, 0.0, 100.0))


def _luminance_comfort_score(mean_l: float, med_l: float) -> float:
    """0–100: wide plateau for typical bench / part lighting; median reduces shadow bias."""
    lum = 0.42 * float(mean_l) + 0.58 * float(med_l)
    if lum < 28.0:
        return float(np.clip(18.0 * lum / 28.0, 0.0, 100.0))
    if lum < 60.0:
        return 18.0 + (lum - 28.0) * (52.0 - 18.0) / 32.0
    if lum < 210.0:
        return 52.0 + (lum - 60.0) * (99.0 - 52.0) / 150.0
    if lum < 238.0:
        return 99.0 - (lum - 210.0) * (99.0 - 48.0) / 28.0
    return float(max(28.0, 48.0 - (lum - 238.0) * 0.9))


def _tonal_spread_score(gray_u8: np.ndarray) -> float:
    """0–100 from inter-percentile spread (robust contrast proxy)."""
    p5, p95 = np.percentile(gray_u8, (5, 95))
    spread = float(p95 - p5) / 255.0
    if spread <= 0.025:
        sc = spread / 0.025 * 22.0
    elif spread <= 0.10:
        sc = 22.0 + (spread - 0.025) / 0.075 * 40.0
    elif spread <= 0.22:
        sc = 62.0 + (spread - 0.10) / 0.12 * 33.0
    else:
        sc = min(100.0, 95.0 + (spread - 0.22) / 0.25 * 5.0)
    return float(np.clip(sc, 0.0, 100.0))


def _exposure_soft_score(gray_u8: np.ndarray) -> float:
    """0–100: gradual penalties for highlights and crushed shadows (multi-threshold)."""
    g = gray_u8.astype(np.float32)
    f_hi = float(np.mean(g >= 252.0))
    f_lo = float(np.mean(g <= 2.0))
    f_hi2 = float(np.mean(g >= 248.0))
    f_lo2 = float(np.mean(g <= 5.0))
    f_hi3 = float(np.mean(g >= 242.0))
    f_lo3 = float(np.mean(g <= 12.0))
    pen = 100.0 * (
        0.52 * (f_hi + f_lo)
        + 0.28 * (max(0.0, f_hi2 - f_hi) + max(0.0, f_lo2 - f_lo))
        + 0.20 * (max(0.0, f_hi3 - f_hi2) + max(0.0, f_lo3 - f_lo2))
    )
    return float(np.clip(100.0 - np.clip(pen, 0.0, 88.0), 0.0, 100.0))


def _saturate_100(x: float, half: float) -> float:
    return float(np.clip(100.0 * x / (x + half), 0.0, 100.0))


def analyze_image_quality_rgb(image: np.ndarray) -> Dict[str, Any]:
    """
    Full quality breakdown for RGB uint8 (or grayscale) images.

    Returns keys (floats, JSON-serializable):
      brightness         — mean gray (full resolution)
      luminance_median   — median gray (shadow-robust reference)
      contrast           — 0–100 tonal spread score
      sharpness          — Laplacian variance on metric-sized gray
      sharpness_index    — 0–100 combined edge-energy score
      exposure           — 0–100 soft clipping / roll-off score
      information        — 0–100 normalized histogram entropy
      score              — weighted composite 0–100
    """
    z = {
        "brightness": 0.0,
        "luminance_median": 0.0,
        "contrast": 0.0,
        "sharpness": 0.0,
        "sharpness_index": 0.0,
        "exposure": 0.0,
        "information": 0.0,
        "score": 0.0,
    }
    try:
        rgb = _ensure_rgb_u8(image)
    except ValueError:
        return z

    gray_full = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray_metric = prepare_gray_for_metrics(rgb)

    mean_l = float(np.mean(gray_full))
    med_l = float(np.median(gray_full))

    exposure = _exposure_soft_score(gray_full)
    lum_s = _luminance_comfort_score(mean_l, med_l)
    con_s = _tonal_spread_score(gray_full)

    lap = laplacian_variance(gray_metric)
    ten = tenengrad_energy_mean(gray_metric)
    lap_i = _saturate_100(lap, 40.0)
    lt = float(np.log1p(max(ten, 1e-6)))
    ten_i = float(np.clip(100.0 * lt / (lt + np.log1p(420.0)), 0.0, 100.0))
    sharp_i = float(np.clip(0.52 * lap_i + 0.48 * ten_i, 0.0, 100.0))

    info = _histogram_entropy_index(gray_full)

    w_exp, w_lum, w_con, w_sha, w_inf = 0.26, 0.16, 0.20, 0.34, 0.04
    score = w_exp * exposure + w_lum * lum_s + w_con * con_s + w_sha * sharp_i + w_inf * info

    # Blank / defocus guards (entropy collapse + tiny Laplacian)
    if info < 12.0 and lap < 1.5:
        score = min(score, 22.0)
    elif info < 18.0 and lap < 4.0:
        score = min(score, max(score * 0.68, 36.0))

    score = float(np.clip(score, 0.0, 100.0))

    return {
        "brightness": mean_l,
        "luminance_median": med_l,
        "contrast": con_s,
        "sharpness": lap,
        "sharpness_index": sharp_i,
        "exposure": exposure,
        "information": info,
        "score": score,
    }
