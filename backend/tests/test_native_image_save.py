"""Master and inspection snapshots are always stored at 1456×1088."""

import numpy as np
import pytest

from src.utils.image_processing import (
    NATIVE_CAPTURE_H,
    NATIVE_CAPTURE_W,
    capture_dimensions_meta,
    ensure_native_capture_rgb,
)


def test_ensure_native_noop_when_already_native():
    img = np.zeros((NATIVE_CAPTURE_H, NATIVE_CAPTURE_W, 3), dtype=np.uint8)
    out, resized = ensure_native_capture_rgb(img)
    assert not resized
    assert out.shape == (NATIVE_CAPTURE_H, NATIVE_CAPTURE_W, 3)


def test_ensure_native_upscales_small_frame():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    out, resized = ensure_native_capture_rgb(img)
    assert resized
    assert out.shape == (NATIVE_CAPTURE_H, NATIVE_CAPTURE_W, 3)
    meta = capture_dimensions_meta(out)
    assert meta['isNativeResolution'] is True


def test_ensure_native_downscales_oversized_frame():
    img = np.zeros((2000, 3000, 3), dtype=np.uint8)
    out, resized = ensure_native_capture_rgb(img)
    assert resized
    assert out.shape[1] == NATIVE_CAPTURE_W
    assert out.shape[0] == NATIVE_CAPTURE_H
