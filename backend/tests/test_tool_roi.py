"""ROI scaling: wizard 640×480 → full-resolution masters."""

import pytest

from src.core.tool_roi import (
    REFERENCE_MASTER_H,
    REFERENCE_MASTER_W,
    ROI_SPACE_NORMALIZED,
    ROI_SPACE_WIZARD,
    infer_template_roi_space,
    normalize_tools_to_master_pixels,
    scale_wizard_roi_to_master,
    validate_template_tools_roi,
    wizard_to_master_scale_factors,
)


def test_wizard_to_imx296_scale_factors():
    sx, sy = wizard_to_master_scale_factors(REFERENCE_MASTER_W, REFERENCE_MASTER_H)
    assert abs(sx - REFERENCE_MASTER_W / 640.0) < 1e-6
    assert abs(sy - REFERENCE_MASTER_H / 480.0) < 1e-6


def test_scale_wizard_roi_to_imx296_master():
    roi = scale_wizard_roi_to_master(
        {'x': 64, 'y': 48, 'width': 320, 'height': 240},
        REFERENCE_MASTER_W,
        REFERENCE_MASTER_H,
    )
    assert roi['x'] == round(64 * REFERENCE_MASTER_W / 640)
    assert roi['y'] == round(48 * REFERENCE_MASTER_H / 480)
    assert roi['width'] == round(320 * REFERENCE_MASTER_W / 640)
    assert roi['height'] == round(240 * REFERENCE_MASTER_H / 480)
    assert roi['x'] + roi['width'] <= REFERENCE_MASTER_W
    assert roi['y'] + roi['height'] <= REFERENCE_MASTER_H


def test_normalize_tools_wizard_to_small_master():
    tools = [
        {
            'name': 'T1',
            'type': 'area',
            'roi': {'x': 64, 'y': 48, 'width': 320, 'height': 240},
        }
    ]
    out = normalize_tools_to_master_pixels(tools, 200, 100, roi_space=ROI_SPACE_WIZARD)
    assert out[0]['roi']['width'] == round(320 * 200 / 640)
    assert out[0]['roi']['height'] == round(240 * 100 / 480)


def test_normalize_tools_normalized_01():
    tools = [
        {
            'name': 'T1',
            'type': 'area',
            'roi': {'x': 0.1, 'y': 0.2, 'width': 0.5, 'height': 0.25},
        }
    ]
    out = normalize_tools_to_master_pixels(tools, 1000, 800, roi_space=ROI_SPACE_NORMALIZED)
    assert out[0]['roi']['x'] == 100
    assert out[0]['roi']['y'] == 160
    assert out[0]['roi']['width'] == 500
    assert out[0]['roi']['height'] == 200


def test_infer_and_validate_wizard_space():
    tools = [{'roi': {'x': 10, 'y': 10, 'width': 100, 'height': 80}}]
    assert infer_template_roi_space(tools) == ROI_SPACE_WIZARD
    assert validate_template_tools_roi(tools) == ROI_SPACE_WIZARD


def test_validate_rejects_roi_outside_wizard_canvas():
    tools = [{'roi': {'x': 600, 'y': 10, 'width': 100, 'height': 80}}]
    with pytest.raises(ValueError, match='outside wizard canvas'):
        validate_template_tools_roi(tools, roi_space=ROI_SPACE_WIZARD)


def test_example_template_rois_fit_canvas():
    """Guards scripts/examples/tool-template.example.json layout."""
    from pathlib import Path

    import json

    path = Path(__file__).resolve().parents[2] / 'scripts/examples/tool-template.example.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    tools = data['tools']
    assert validate_template_tools_roi(tools, roi_space=data.get('roi_space')) == ROI_SPACE_WIZARD
    scaled = normalize_tools_to_master_pixels(
        tools, REFERENCE_MASTER_W, REFERENCE_MASTER_H, roi_space=ROI_SPACE_WIZARD
    )
    for t in scaled:
        r = t['roi']
        assert r['width'] >= 2 and r['height'] >= 2
        assert r['x'] + r['width'] <= REFERENCE_MASTER_W
        assert r['y'] + r['height'] <= REFERENCE_MASTER_H
