"""
REST API for a remote Raspberry Pi (master) to control the vision slave over Ethernet.

Programs: use existing /api/programs or /api/v1/programs (same host). This blueprint adds:
  - Discovery + capability metadata
  - One-shot inspection with JSON + base64 image (no Socket.IO required)

Continuous inspection and live MJPEG-style streaming remain on Socket.IO:
  start_inspection, subscribe_live_feed (see scripts/vision_master_client.py).
"""

import traceback
from typing import Any, Optional

from flask import Blueprint, current_app, jsonify, request

from src.core.inspection_runner import run_inspection_once
from src.core.program_manager import ProgramManager
from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController
from src.database.db_manager import DatabaseManager
from src.utils.logger import get_logger
from src.utils.remote_auth import require_remote_key

logger = get_logger("remote_api")

remote_bp = Blueprint("remote", __name__)

program_manager: ProgramManager = None
camera_controller: CameraController = None
gpio_controller: GPIOController = None
lighting_controller: Optional[Any] = None
lighting_global_config: dict = {}
db_manager: DatabaseManager = None


def init_remote_api(
    pm: ProgramManager,
    cam: CameraController,
    gpio: GPIOController,
    db: DatabaseManager,
    lighting=None,
    lighting_global: dict = None,
):
    global program_manager, camera_controller, gpio_controller, db_manager
    global lighting_controller, lighting_global_config
    program_manager = pm
    camera_controller = cam
    gpio_controller = gpio
    db_manager = db
    lighting_controller = lighting
    lighting_global_config = dict(lighting_global or {})
    logger.info("Remote agent API initialized")


@remote_bp.route("/info", methods=["GET"])
def remote_info():
    """
    Public discovery: system name, REST prefix, Socket.IO path, auth requirement.
    """
    need_key = bool((current_app.config.get("REMOTE_API_KEY") or "").strip())
    sys_block = current_app.config.get("system")
    system_name = sys_block.get("name") if isinstance(sys_block, dict) else None
    if not system_name:
        system_name = current_app.config.get("APP_NAME")
    sk_auth = bool((current_app.config.get("REMOTE_SOCKETIO_AUTH_KEY") or "").strip())
    return jsonify(
        {
            "role": "vision_inspection_slave",
            "system_name": system_name,
            "socketio_path": "/socket.io/",
            "socketio_connect_auth_required": sk_auth,
            "socketio_connect_auth": (
                "Pass auth={remoteKey: '<same as X-Vision-Remote-Key>'} on connect when required"
                if sk_auth
                else None
            ),
            "socketio_events": {
                "start_inspection": {"programId": "int", "continuous": "bool"},
                "stop_inspection": {},
                "subscribe_live_feed": {"fps": "int optional"},
                "unsubscribe_live_feed": {},
            },
            "rest": {
                "programs": "GET/POST /programs",
                "program": "GET/PUT/DELETE /programs/:id",
                "run_once": "POST /remote/inspection/run-once (may require X-Vision-Remote-Key)",
                "camera_capture": "POST /camera/capture",
                "health": "GET /health (same REST prefix as this blueprint: /api/... or /api/v1/...)",
            },
            "remote_auth_required": need_key,
            "require_remote_api_key_configured": bool(
                current_app.config.get("SLAVE_REQUIRE_REMOTE_API_KEY")
            ),
            "require_real_hardware": bool(current_app.config.get("SLAVE_REQUIRE_REAL_HARDWARE")),
        }
    ), 200


@remote_bp.route("/inspection/run-once", methods=["POST"])
@require_remote_key
def remote_run_inspection_once():
    """
    Run a single inspection cycle; return status, tools, timing, optional image.

    Body JSON:
      programId (required)
      triggerType (optional, default 'remote') — stored in inspection_results
      includeImage (optional, default true) — omit large payload for PLC use
    """
    try:
        data = request.get_json(silent=True) or {}
        program_id = data.get("programId")
        if program_id is None:
            return jsonify({"error": "programId is required"}), 400
        program_id = int(program_id)

        trigger_type = data.get("triggerType") or "remote"
        include_image = data.get("includeImage", True)

        require_hw = bool(current_app.config.get("SLAVE_REQUIRE_REAL_HARDWARE"))

        try:
            payload = run_inspection_once(
                program_manager=program_manager,
                camera_controller=camera_controller,
                gpio_controller=gpio_controller,
                lighting_controller=lighting_controller,
                lighting_global_config=lighting_global_config,
                db_manager=db_manager,
                program_id=program_id,
                trigger_type=str(trigger_type),
                include_image=bool(include_image),
                persist_result=True,
                require_real_hardware=require_hw,
            )
        except ValueError as e:
            msg = str(e)
            code = 404 if "not found" in msg.lower() else 400
            return jsonify({"error": msg}), code
        except RuntimeError as e:
            msg = str(e)
            if "Hardware not ready" in msg or "CSI camera unavailable" in msg:
                return (
                    jsonify(
                        {
                            "error": "Hardware not ready",
                            "detail": msg,
                        }
                    ),
                    503,
                )
            return jsonify({"error": "Inspection failed", "detail": msg}), 500

        return jsonify(payload), 200

    except Exception as e:
        logger.error("remote run-once failed: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": "Inspection failed", "detail": str(e)}), 500
