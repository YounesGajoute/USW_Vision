"""Single-shot inspection execution shared by local API and remote PLC API."""

import os
import traceback
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.core.inspection_engine import InspectionEngine
from src.core.program_manager import ProgramManager
from src.core.tool_roi import (
    infer_template_roi_space,
    master_image_dimensions,
    normalize_tools_to_master_pixels,
)
from src.database.db_manager import DatabaseManager
from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController
from src.utils.image_processing import ARCHIVE_IMAGE_FORMAT, numpy_to_base64
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.tool_template_manager import ToolTemplateManager

logger = get_logger("inspection_runner")

DEFAULT_TEMPLATE_OUTPUTS = {
    "OUT1": "Always ON",
    "OUT2": "OK",
    "OUT3": "NG",
    "OUT4": "Not Used",
    "OUT5": "Not Used",
    "OUT6": "Not Used",
    "OUT7": "Not Used",
    "OUT8": "Not Used",
}


def build_engine_config_for_program(program: Dict) -> Dict[str, Any]:
    """Build InspectionEngine config dict shared by REST and WebSocket paths."""
    import cv2

    cfg = dict(program["config"])
    cfg["id"] = program["id"]
    cfg["name"] = program["name"]
    master_path = resolve_master_image_path_for_engine(program)
    cfg["masterImage"] = master_path

    tools = list(cfg.get("tools") or [])
    if tools:
        master_bgr = cv2.imread(master_path)
        if master_bgr is not None:
            master_rgb = cv2.cvtColor(master_bgr, cv2.COLOR_BGR2RGB)
            mw, mh = master_image_dimensions(master_rgb)
            roi_space = cfg.get("toolsRoiSpace") or infer_template_roi_space(tools)
            cfg["tools"] = normalize_tools_to_master_pixels(
                tools, mw, mh, roi_space=roi_space
            )

    return cfg


def resolve_master_image_path_for_engine(program: Dict) -> str:
    """
    InspectionEngine.load_program reads master from config['masterImage'] as a filesystem path.
    Prefer the DB-backed master_image_path when present.
    """
    db_path = program.get("master_image_path")
    if isinstance(db_path, str) and db_path and os.path.isfile(db_path):
        return db_path

    cfg = program.get("config") or {}
    cpath = cfg.get("masterImage")
    if isinstance(cpath, str) and cpath and not cpath.startswith("data:"):
        if os.path.isfile(cpath):
            return cpath

    raise ValueError(
        "Master image is not available as a file on disk. "
        "Save the program from the wizard so the master is persisted, then retry."
    )


def _check_hardware_ready(camera_controller: CameraController, require_real_hardware: bool) -> None:
    if not require_real_hardware:
        return
    cam_info = camera_controller.get_camera_info()
    if cam_info.get("simulated"):
        raise RuntimeError(
            "Hardware not ready: CSI camera unavailable while slave.require_real_hardware is enabled"
        )


def run_inspection_with_engine_config(
    *,
    engine_config: Dict[str, Any],
    program_manager: ProgramManager,
    camera_controller: CameraController,
    gpio_controller: GPIOController,
    lighting_controller: Optional[Any],
    lighting_global_config: Dict[str, Any],
    db_manager: DatabaseManager,
    trigger_type: str = "internal",
    include_image: bool = True,
    persist_result: bool = True,
    persist_program_id: Optional[int] = None,
    persist_notes: Optional[str] = None,
    snapshot_program_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Run one cycle with a fully-built engine config dict."""
    engine = InspectionEngine(
        engine_config,
        camera=camera_controller,
        gpio=gpio_controller,
        lighting=lighting_controller,
        lighting_global=lighting_global_config or {},
        release_hardware_on_cleanup=False,
    )

    try:
        status, tool_results, processing_time, image = engine.run_inspection_cycle()
    except Exception as exc:
        logger.error("Inspection cycle failed: %s\n%s", exc, traceback.format_exc())
        raise RuntimeError(str(exc)) from exc

    snap_id = snapshot_program_id if snapshot_program_id is not None else persist_program_id
    image_path = None
    if persist_result and image is not None and snap_id is not None:
        try:
            image_path = program_manager.save_inspection_snapshot(snap_id, status, image)
        except Exception as save_err:
            logger.warning("Failed to save inspection snapshot: %s", save_err)

    result_id = None
    if persist_result and persist_program_id is not None:
        result_id = db_manager.log_inspection_result(
            program_id=persist_program_id,
            status=status,
            processing_time_ms=processing_time,
            tool_results=tool_results,
            trigger_type=str(trigger_type),
            image_path=image_path,
            notes=persist_notes,
        )

    payload: Dict[str, Any] = {
        "programId": persist_program_id,
        "programName": engine_config.get("name", "Inspection"),
        "status": status,
        "toolResults": tool_results,
        "processingTimeMs": processing_time,
        "resultId": result_id,
        "triggerType": trigger_type,
    }
    if include_image and image is not None:
        from src.utils.image_processing import capture_dimensions_meta, ensure_native_capture_rgb

        image, _ = ensure_native_capture_rgb(image)
        payload["image"] = numpy_to_base64(image, format=ARCHIVE_IMAGE_FORMAT)
        payload["imageFormat"] = ARCHIVE_IMAGE_FORMAT
        dims = capture_dimensions_meta(image)
        payload["width"] = dims["width"]
        payload["height"] = dims["height"]
        payload["isNativeResolution"] = dims["isNativeResolution"]

    return payload


def run_inspection_once(
    *,
    program_manager: ProgramManager,
    camera_controller: CameraController,
    gpio_controller: GPIOController,
    lighting_controller: Optional[Any],
    lighting_global_config: Dict[str, Any],
    db_manager: DatabaseManager,
    program_id: int,
    trigger_type: str = "internal",
    include_image: bool = True,
    persist_result: bool = True,
    require_real_hardware: bool = False,
) -> Dict[str, Any]:
    """
    Run one InspectionEngine cycle for a saved program; optionally log to DB and return payload.
    """
    _check_hardware_ready(camera_controller, require_real_hardware)

    program = program_manager.get_program(program_id)
    if not program:
        raise ValueError(f"Program {program_id} not found")

    cfg = build_engine_config_for_program(program)

    payload = run_inspection_with_engine_config(
        engine_config=cfg,
        program_manager=program_manager,
        camera_controller=camera_controller,
        gpio_controller=gpio_controller,
        lighting_controller=lighting_controller,
        lighting_global_config=lighting_global_config,
        db_manager=db_manager,
        trigger_type=trigger_type,
        include_image=include_image,
        persist_result=persist_result,
        persist_program_id=program_id if persist_result else None,
        snapshot_program_id=program_id,
    )
    payload["programId"] = program_id
    payload["programName"] = program["name"]
    payload["runMode"] = "program"
    return payload


def build_engine_config_for_template_run(
    program: Dict,
    template_id: int,
    tool_template_manager: "ToolTemplateManager",
) -> Dict[str, Any]:
    """Build InspectionEngine config for template + program master (REST/WS shared)."""
    import cv2

    master_path = resolve_master_image_path_for_engine(program)
    master_bgr = cv2.imread(master_path)
    if master_bgr is None:
        raise ValueError(f"Could not read master image: {master_path}")
    master_rgb = cv2.cvtColor(master_bgr, cv2.COLOR_BGR2RGB)
    mw, mh = master_image_dimensions(master_rgb)

    template = tool_template_manager.get_template(template_id, include_image=False)
    if not template:
        raise ValueError(f"Template {template_id} not found")

    roi_space = template.get("roi_space") or "wizard_640x480"
    scaled_tools = normalize_tools_to_master_pixels(
        template.get("tools") or [],
        mw,
        mh,
        roi_space=roi_space,
    )

    prog_cfg = dict(program.get("config") or {})
    return {
        "id": program["id"],
        "name": f"{program['name']} + {template.get('name', 'template')}",
        "masterImage": master_path,
        "tools": scaled_tools,
        "outputs": prog_cfg.get("outputs") or DEFAULT_TEMPLATE_OUTPUTS,
        "triggerType": prog_cfg.get("triggerType", "internal"),
        "brightnessMode": prog_cfg.get("brightnessMode", "normal"),
        "focusValue": prog_cfg.get("focusValue", 50),
        "exposureTimeUs": prog_cfg.get("exposureTimeUs", 5000),
        "analogGain": prog_cfg.get("analogGain", 1.0),
        "digitalGain": prog_cfg.get("digitalGain", 1.0),
    }


def run_inspection_with_template(
    *,
    program_manager: ProgramManager,
    tool_template_manager: "ToolTemplateManager",
    camera_controller: CameraController,
    gpio_controller: GPIOController,
    lighting_controller: Optional[Any],
    lighting_global_config: Dict[str, Any],
    db_manager: DatabaseManager,
    template_id: int,
    program_id: Optional[int] = None,
    trigger_type: str = "internal",
    include_image: bool = True,
    persist_result: bool = True,
    require_real_hardware: bool = False,
) -> Dict[str, Any]:
    """
    Run inspection using a tool template + master from an existing program.

    Template ROIs (wizard 640×480) are scaled to the program master dimensions before
    the engine loads tools.
    """
    _check_hardware_ready(camera_controller, require_real_hardware)

    if program_id is None:
        raise ValueError("programId is required when running with a tool template")

    program = program_manager.get_program(program_id)
    if not program:
        raise ValueError(f"Program {program_id} not found")

    engine_config = build_engine_config_for_template_run(
        program, template_id, tool_template_manager
    )
    template = tool_template_manager.get_template(template_id, include_image=False)
    master_path = engine_config["masterImage"]
    scaled_tools = engine_config["tools"]

    notes = f"template_id={template_id}; template={template.get('name', '')}"
    payload = run_inspection_with_engine_config(
        engine_config=engine_config,
        program_manager=program_manager,
        camera_controller=camera_controller,
        gpio_controller=gpio_controller,
        lighting_controller=lighting_controller,
        lighting_global_config=lighting_global_config,
        db_manager=db_manager,
        trigger_type=trigger_type,
        include_image=include_image,
        persist_result=persist_result,
        persist_program_id=program_id if persist_result else None,
        persist_notes=notes if persist_result else None,
        snapshot_program_id=program_id,
    )

    payload["programId"] = program_id
    payload["programName"] = program["name"]
    payload["templateId"] = template_id
    payload["templateName"] = template.get("name")
    payload["runMode"] = "template"
    import cv2

    master_bgr = cv2.imread(master_path)
    if master_bgr is not None:
        master_rgb = cv2.cvtColor(master_bgr, cv2.COLOR_BGR2RGB)
        mw, mh = master_image_dimensions(master_rgb)
        payload["masterSize"] = {"width": mw, "height": mh}
    payload["masterImagePath"] = master_path
    payload["toolCount"] = len(scaled_tools)
    return payload
