"""Tests for scripts/measure_welding_splice_sleeve.py (welding splice + heat-shrink sleeve)."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

from measure_welding_splice_sleeve import (  # noqa: E402
    _detect_sleeve_mask_reference,
    _pca_measurement,
    _trim_sleeve_contour_to_body,
    detect_cable_band,
    load_px_per_mm,
    load_sleeve_reference,
    measure_image,
    measure_sleeve,
)

PX = 5.1
INSP = _REPO / "backend/storage/image_history/12/insp_OK_20260515_121850_664453.png"
MASTER = _REPO / "backend/storage/master_images/program_12.png"
NO_SLEEVE = (
    _REPO
    / "backend/storage/Measurement/session_20260517_090020/capture.png"
)
SLEEVE_ONLY = (
    _REPO
    / "backend/storage/Measurement/session_20260517_090918/capture.png"
)
WELDING_SPLICE_ONLY = (
    _REPO
    / "backend/storage/Measurement/session_20260517_092823/capture.png"
)
ASSEMBLY_LONG_SLEEVE = (
    _REPO
    / "backend/storage/Measurement/session_20260517_114604/capture.png"
)
CAL = _REPO / "backend/storage/Calibration/session_20260517_081705"
DATA_CAPTURES = sorted((_REPO / "backend/storage/Measurement/Data").glob("capture*.png"))


@pytest.mark.skipif(not INSP.is_file(), reason="inspection sample missing")
def test_inspection_welding_splice_dimensions():
    img = cv2.imread(str(INSP))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(INSP), px, "test")
    assert result.welding_splice and result.welding_splice.found
    m = result.welding_splice.measurement
    assert 14 <= m.length_mm <= 24
    assert 3 <= m.height_mm <= 8


@pytest.mark.skipif(not INSP.is_file(), reason="inspection sample missing")
def test_inspection_sleeve_dimensions():
    img = cv2.imread(str(INSP))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(INSP), px, "test")
    assert result.sleeve and result.sleeve.found
    m = result.sleeve.measurement
    assert 15 <= m.length_mm <= 38
    assert 4 <= m.height_mm <= 14
    assert m.length_mm > m.height_mm


@pytest.mark.skipif(not NO_SLEEVE.is_file(), reason="no-sleeve capture missing")
def test_no_sleeve_capture_rejects_false_positive():
    """Two exposed welding splices, no heat-shrink — must not report a sleeve."""
    img = cv2.imread(str(NO_SLEEVE))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(NO_SLEEVE), px, "test")
    assert result.welding_splice and result.welding_splice.found
    assert result.sleeve is not None and not result.sleeve.found
    assert result.sleeve.debug.get("reject") == "od_below_min"


@pytest.mark.skipif(not WELDING_SPLICE_ONLY.is_file(), reason="welding-splice-only capture missing")
def test_welding_splice_only_capture():
    """Exposed welding splice, no heat-shrink sleeve."""
    img = cv2.imread(str(WELDING_SPLICE_ONLY))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(WELDING_SPLICE_ONLY), px, "test")
    assert result.welding_splice and result.welding_splice.found
    assert result.welding_splice.debug.get("mode") == "welding_splice_only_image"
    assert result.welding_splice.debug.get("refined_full_welding_splice") is True
    assert result.sleeve is not None and not result.sleeve.found
    m = result.welding_splice.measurement
    assert 17 <= m.length_mm <= 20
    assert 4.5 <= m.height_mm <= 8.5
    assert result.welding_splice.debug.get("refined_full_welding_splice") is True
    assert "heat-shrink sleeve not detected" not in result.errors


@pytest.mark.skipif(
    not ASSEMBLY_LONG_SLEEVE.is_file(), reason="assembly sleeve regression capture missing"
)
def test_assembly_sleeve_excludes_wire_bundle():
    """Matte span clip must not include black wires left of the heat-shrink body."""
    img = cv2.imread(str(ASSEMBLY_LONG_SLEEVE))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(ASSEMBLY_LONG_SLEEVE), px, "test")
    assert result.sleeve and result.sleeve.found
    assert result.sleeve.debug.get("method") == "reference"
    m = result.sleeve.measurement
    assert 50 <= m.length_mm <= 80
    assert 10 <= m.height_mm <= 18
    assert m.length_mm > m.height_mm * 3


@pytest.mark.skipif(not SLEEVE_ONLY.is_file(), reason="sleeve-only capture missing")
def test_sleeve_only_capture():
    """Isolated heat-shrink sleeve, no welding splice."""
    img = cv2.imread(str(SLEEVE_ONLY))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(SLEEVE_ONLY), px, "test")
    assert result.welding_splice is not None and not result.welding_splice.found
    assert result.welding_splice.debug.get("mode") == "sleeve_only_image"
    assert result.sleeve and result.sleeve.found
    assert result.sleeve.debug.get("method") in ("standalone", "reference")
    assert result.sleeve.debug.get("refined_full_tube") is True
    m = result.sleeve.measurement
    assert 64 <= m.length_mm <= 68
    assert 12.5 <= m.height_mm <= 14.5
    assert m.length_mm > m.height_mm
    assert "welding splice not detected" not in result.errors


@pytest.mark.skipif(not MASTER.is_file(), reason="master sample missing")
def test_master_sleeve_found():
    img = cv2.imread(str(MASTER))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_image(img, str(MASTER), px, "test")
    assert result.sleeve and result.sleeve.found


def test_pca_axis_length_greater_than_height_for_rectangle():
    # 100x20 px rectangle → length ~100, height ~20
    rect = np.array(
        [[[0, 0], [100, 0], [100, 20], [0, 20]]], dtype=np.int32
    )
    m = _pca_measurement(rect, PX)
    assert m.length_px == pytest.approx(100, rel=0.05)
    assert m.height_px == pytest.approx(20, rel=0.15)


@pytest.mark.skipif(not (_REPO / "backend/storage/reference/sleeve/sleeve_reference.json").is_file(), reason="sleeve reference missing")
def test_sleeve_reference_profile_loaded():
    ref = load_sleeve_reference()
    assert ref is not None
    assert "capture5.png" in ref.get("sources", [])
    assert ref.get("gray_max", 0) >= 60


@pytest.mark.skipif(not (_REPO / "backend/storage/Measurement/Data/capture8.png").is_file(), reason="capture8 missing")
def test_reference_mask_finds_full_tube_capture8():
    img = cv2.imread(str(_REPO / "backend/storage/Measurement/Data/capture8.png"))
    y0, y1 = detect_cable_band(img)
    mask, dbg = _detect_sleeve_mask_reference(img, y0, y1)
    assert mask is not None, dbg
    assert dbg.get("method") == "reference"
    assert dbg.get("refined_full_tube") is True
    x, y, bw, bh = dbg["aabb"]
    assert bw >= 280, f"tube too short in x: {bw}px"
    assert 60 <= bw / PX <= 72


@pytest.mark.skipif(not DATA_CAPTURES, reason="Measurement/Data captures missing")
@pytest.mark.parametrize("capture_path", DATA_CAPTURES, ids=lambda p: p.name)
def test_data_capture_sleeve_found(capture_path: Path):
    """Regression: all Measurement/Data tube images must yield a sleeve."""
    img = cv2.imread(str(capture_path))
    px, _ = load_px_per_mm(CAL, None)
    result = measure_sleeve(img, str(capture_path), px, "test")
    assert result.sleeve and result.sleeve.found, result.sleeve.debug if result.sleeve else {}
    m = result.sleeve.measurement
    assert 52 <= m.length_mm <= 88, capture_path.name
    assert 10 <= m.height_mm <= 20, capture_path.name
    assert m.length_mm > m.height_mm * 2.5


def test_trim_shortens_elongated_contour():
    pts = []
    for x in range(0, 200, 2):
        for y in range(88, 112):
            pts.append([x, y])
    for x in range(200, 280):
        for y in range(95, 105):
            pts.append([x, y])
    contour = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
    trimmed = _trim_sleeve_contour_to_body(contour, PX, max_length_mm=25)
    m = _pca_measurement(trimmed, PX)
    assert m.length_mm <= 28
