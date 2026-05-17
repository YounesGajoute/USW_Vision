"""Unit tests for inspection_runner engine config helpers (no camera)."""

import os
import tempfile

import pytest

from src.core.inspection_runner import (
    build_engine_config_for_program,
    resolve_master_image_path_for_engine,
)


def test_resolve_master_prefers_db_path():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        path = f.name
    try:
        program = {
            "id": 1,
            "name": "Test",
            "master_image_path": path,
            "config": {"masterImage": "/nonexistent/other.png", "tools": []},
        }
        assert resolve_master_image_path_for_engine(program) == path
    finally:
        os.unlink(path)


def test_build_engine_config_for_program():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        path = f.name
    try:
        program = {
            "id": 42,
            "name": "Widget",
            "master_image_path": path,
            "config": {
                "masterImage": "stale",
                "tools": [{"name": "t1"}],
                "triggerInterval": 1500,
            },
        }
        cfg = build_engine_config_for_program(program)
        assert cfg["id"] == 42
        assert cfg["name"] == "Widget"
        assert cfg["masterImage"] == path
        assert cfg["tools"] == [{"name": "t1"}]
        assert cfg["triggerInterval"] == 1500
    finally:
        os.unlink(path)


def test_resolve_master_raises_when_missing():
    program = {
        "id": 1,
        "name": "X",
        "config": {"masterImage": "data:image/png;base64,abc"},
    }
    with pytest.raises(ValueError, match="Master image"):
        resolve_master_image_path_for_engine(program)
