"""
REST API for a remote Raspberry Pi (master) to control the vision slave over Ethernet.

Programs: use existing /api/programs or /api/v1/programs (same host). This blueprint adds:
  - Discovery + capability metadata
  - One-shot inspection with JSON + base64 image (no Socket.IO required)

Continuous inspection and live MJPEG-style streaming remain on Socket.IO:
  start_inspection, subscribe_live_feed (see scripts/vision_master_client.py).
"""

import time
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
from src.api.health import check_camera
from src.api.websocket import stop_all_live_feeds

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
                "subscribe_live_feed": {
                    "fps": "int optional",
                    "fullResolution": "bool optional",
                    "useCaptureSettings": "bool optional (default true)",
                    "brightnessMode": "normal|hdr|highgain optional",
                    "exposureTime": "µs int optional",
                    "analogGain": "float optional",
                    "digitalGain": "float optional",
                },
                "unsubscribe_live_feed": {},
            },
            "rest": {
                "programs": "GET/POST /programs",
                "program": "GET/PUT/DELETE /programs/:id (or DELETE /remote/programs/:id)",
                "run_once": "POST /remote/inspection/run-once (may require X-Vision-Remote-Key)",
                "camera_recover": "POST /remote/camera/recover (stop live feeds + reopen CSI camera)",
                "camera_capture": "POST /camera/capture",
                "health": "GET /health (same REST prefix as this blueprint: /api/... or /api/v1/...)",
                "health_full": "GET /health/full?probe=capture",
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


@remote_bp.route("/programs/<int:program_id>", methods=["DELETE"])
@require_remote_key
def remote_delete_program(program_id: int):
    """
    DELETE /api/remote/programs/:id
    Permanently delete a program from the master Pi (same as DELETE /api/programs/:id).
    """
    try:
        if tool_template_manager:
            try:
                tool_template_manager.delete_program_template(program_id)
            except Exception as exc:
                logger.warning("Could not delete program-owned template: %s", exc)

        program_manager.delete_program(program_id)
        return jsonify({"message": "Program deleted successfully", "programId": program_id}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error("remote delete program failed: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": "Delete failed", "detail": str(e)}), 500


@remote_bp.route("/camera/recover", methods=["POST"])
@require_remote_key
def remote_camera_recover():
    """
    Recover CSI camera after libcamera pipeline timeout (master Pi operator action).

    Body JSON (all optional):
      stopLiveFeeds (bool, default true) — stop Socket.IO live preview threads first
      probeCapture (bool, default true) — grab one test frame after reconnect
    """
    try:
        data = request.get_json(silent=True) or {}
        stop_feeds = data.get("stopLiveFeeds", True)
        probe_capture = data.get("probeCapture", True)

        feeds_stopped = stop_all_live_feeds() if stop_feeds else 0
        time.sleep(0.25)

        reopened = False
        if camera_controller is not None:
            reopened = bool(camera_controller.recover_camera())
        else:
            return jsonify({"error": "Camera controller not initialized"}), 503

        cam_health = check_camera(probe_capture=bool(probe_capture))
        ok = reopened and cam_health.get("status") == "healthy"

        payload = {
            "message": "Camera recovered" if ok else "Camera recover attempted; check camera status",
            "recovered": ok,
            "pipelineReopened": reopened,
            "liveFeedsStopped": feeds_stopped,
            "camera": cam_health,
        }
        return jsonify(payload), 200 if ok else 503

    except Exception as e:
        logger.error("remote camera recover failed: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": "Camera recover failed", "detail": str(e)}), 500
