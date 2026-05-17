"""build_engine_config_for_template_run and program tool scaling (no camera)."""

import json
import os
import tempfile

import pytest

from src.core.inspection_runner import (
    build_engine_config_for_program,
    build_engine_config_for_template_run,
)


class _FakeTemplateManager:
    def __init__(self, template):
        self._template = template

    def get_template(self, template_id, include_image=False):
        if template_id == self._template.get("id"):
            return self._template
        return None


def test_build_engine_config_for_template_run_scales_tools():
    import cv2
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        master_path = os.path.join(tmp, "master.png")
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        cv2.imwrite(master_path, img)

        program = {
            "id": 1,
            "name": "Prog",
            "master_image_path": master_path,
            "config": {
                "outputs": {},
                "triggerType": "internal",
            },
        }
        template = {
            "id": 5,
            "name": "Tmpl",
            "roi_space": "wizard_640x480",
            "tools": [
                {
                    "name": "T1",
                    "type": "pattern_match",
                    "roi": {"x": 64, "y": 48, "width": 320, "height": 240},
                }
            ],
        }
        ttm = _FakeTemplateManager(template)
        cfg = build_engine_config_for_template_run(program, 5, ttm)
        assert cfg["masterImage"] == master_path
        assert len(cfg["tools"]) == 1
        assert cfg["tools"][0]["name"] == "T1"
        # ROI scaled from 640x480 space to 200x100 master
        assert cfg["tools"][0]["roi"]["width"] > 0


def test_build_engine_config_for_program_scales_wizard_rois():
    import cv2
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        master_path = os.path.join(tmp, "master.png")
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        cv2.imwrite(master_path, img)

        program = {
            "id": 2,
            "name": "Prog2",
            "master_image_path": master_path,
            "config": {
                "toolsRoiSpace": "wizard_640x480",
                "tools": [
                    {
                        "name": "T1",
                        "type": "area",
                        "roi": {"x": 64, "y": 48, "width": 320, "height": 240},
                        "threshold": 80,
                    }
                ],
                "outputs": {},
            },
        }
        cfg = build_engine_config_for_program(program)
        roi = cfg["tools"][0]["roi"]
        assert roi["width"] == round(320 * 200 / 640)
        assert roi["height"] == round(240 * 100 / 480)
        assert cfg["tools"][0]["threshold"] == 80
