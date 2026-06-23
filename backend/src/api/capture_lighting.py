"""Shared illumination helpers for REST capture and Socket.IO live preview."""

import time
from typing import Any, Dict, Optional


def api_lighting_on(lighting_controller: Any, settings: Dict[str, Any]) -> bool:
    """Return True if illumination was applied (caller should call api_lighting_off_if)."""
    if not lighting_controller or not lighting_controller.is_ready():
        return False
    if not settings.get("use_for_api_capture", True):
        return False
    rgb = settings.get("default_rgb", [255, 255, 255])
    r, g, b = (int(rgb[0]) & 0xFF, int(rgb[1]) & 0xFF, int(rgb[2]) & 0xFF)
    lighting_controller.fill(r, g, b)
    lighting_controller.show()
    time.sleep(float(settings.get("settle_ms", 2.0)) / 1000.0)
    return True


def api_lighting_off_if(applied: bool, lighting_controller: Any, settings: Dict[str, Any]) -> None:
    if not applied or not lighting_controller or not lighting_controller.is_ready():
        return
    if not settings.get("off_after_capture", True):
        return
    lighting_controller.off()
