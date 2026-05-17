"""REST API Routes for Vision Inspection System"""

from flask import Blueprint, request, jsonify, send_file, current_app
from werkzeug.utils import secure_filename
import os
import base64
import mimetypes
import traceback
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from src.core.program_manager import ProgramManager
from src.core.tool_template_manager import ToolTemplateManager
from src.core.inspection_runner import (
    run_inspection_once,
    run_inspection_with_template,
    resolve_master_image_path_for_engine,
)
from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController
from src.database.db_manager import DatabaseManager
from src.hardware.p9813_lighting import describe_lighting_controller
from src.utils.validators import validate_json_request, validate_file_upload, sanitize_filename
from src.utils.image_processing import (
    ARCHIVE_IMAGE_FORMAT,
    base64_to_numpy,
    numpy_to_base64,
)
from src.utils.logger import get_logger

logger = get_logger('api')

# Create API blueprint
api = Blueprint('api', __name__)

_LOCAL_PROTECTED_PREFIXES = (
    '/programs',
    '/inspection',
    '/gpio',
    '/camera/capture',
    '/master-image',
    '/inspections',
    '/tool-templates',
)


@api.before_request
def _enforce_local_api_key():
    """When LOCAL_API_KEY is set, require it on sensitive routes."""
    from src.utils.local_auth import verify_local_api_key

    path = request.path or ''
    rel = path[len('/api') :] if path.startswith('/api') else path
    if not any(rel.startswith(p) for p in _LOCAL_PROTECTED_PREFIXES):
        return None
    if path.endswith('/health') or '/health/' in path:
        return None
    if not verify_local_api_key():
        return jsonify(
            {'error': 'Unauthorized', 'hint': 'Send X-Vision-Local-Key or Authorization: Bearer'}
        ), 401
    return None


# ==================== INTERNAL HELPERS ====================

def _is_base64_image(value: str) -> bool:
    """Return True when *value* looks like raw/data-URI base64 rather than a file path.

    Master images saved to disk always end with '.png' or '.jpg'.
    Base64-encoded JPEG data is hundreds of kB long and has no such extension.
    """
    if not value:
        return False
    if value.startswith('data:'):
        return True
    lower = value.lower()
    # Saved file paths always carry an image extension
    if lower.endswith('.png') or lower.endswith('.jpg') or lower.endswith('.jpeg'):
        return False
    # Very long strings without an image extension are raw base64
    return len(value) > 300


def _save_master_image_from_config(program_id: int, config: dict):
    """
    If config['masterImage'] holds base64 data instead of a file path,
    decode it, write it to the image-history storage, and update the DB row
    so future lookups find the file on disk.
    Safe to call even when no image is present.
    """
    master_value = (config or {}).get('masterImage', '')
    if not master_value or not _is_base64_image(master_value):
        return

    try:
        import numpy as np
        import cv2 as _cv2

        b64 = master_value
        if ',' in b64:
            b64 = b64.split(',', 1)[1]

        image_bytes = base64.b64decode(b64)
        nparr = np.frombuffer(image_bytes, np.uint8)
        image_bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)

        if image_bgr is None:
            logger.warning(f"Could not decode master image base64 for program {program_id}")
            return

        image_rgb = _cv2.cvtColor(image_bgr, _cv2.COLOR_BGR2RGB)
        saved_path = program_manager.save_master_image(program_id, image_rgb)
        logger.info(f"Master image saved to disk for program {program_id}: {saved_path}")

    except Exception as exc:
        logger.warning(f"Failed to persist master image for program {program_id}: {exc}")


def _sync_program_owned_template(program_id: int, program_name: str, config: dict) -> Optional[int]:
    """
    Each program gets its own tool template named after the program.
    Updates only that program's template — never another program's (e.g. test121 vs 1222).
  """
    if not tool_template_manager or not program_manager:
        return None

    tools = (config or {}).get('tools') or []
    if not tools:
        return (config or {}).get('toolTemplateId')

    try:
        template = tool_template_manager.upsert_program_template(
            program_id=program_id,
            program_name=program_name,
            tools=tools,
            template_id_hint=(config or {}).get('toolTemplateId'),
            roi_space=(config or {}).get('toolsRoiSpace'),
        )
        template_id = int(template['id'])
        if (config or {}).get('toolTemplateId') != template_id or not (config or {}).get(
            'toolTemplateOwned'
        ):
            merged = dict(config or {})
            merged['toolTemplateId'] = template_id
            merged['toolTemplateOwned'] = True
            program_manager.update_program(program_id, {'config': merged})
        return template_id
    except Exception as exc:
        logger.warning(
            'Failed to sync program-owned template for program %s: %s', program_id, exc
        )
        return (config or {}).get('toolTemplateId')


# Global instances (will be initialized by app factory)
program_manager: ProgramManager = None
tool_template_manager: ToolTemplateManager = None
camera_controller: CameraController = None
gpio_controller: GPIOController = None
db_manager: Optional[DatabaseManager] = None
lighting_controller: Optional[Any] = None
_lighting_api_settings: Dict[str, Any] = {}
_lighting_global_config: Dict[str, Any] = {}


def init_api(
    pm: ProgramManager,
    cam: CameraController,
    gpio: GPIOController,
    lighting: Optional[Any] = None,
    lighting_settings: Optional[Dict[str, Any]] = None,
    db: Optional[DatabaseManager] = None,
    lighting_global: Optional[Dict[str, Any]] = None,
    tool_templates: Optional[ToolTemplateManager] = None,
):
    """Initialize API with dependencies."""
    global program_manager, tool_template_manager, camera_controller, gpio_controller
    global lighting_controller, _lighting_api_settings, db_manager
    global _lighting_global_config
    program_manager = pm
    tool_template_manager = tool_templates
    camera_controller = cam
    gpio_controller = gpio
    db_manager = db
    lighting_controller = lighting
    _lighting_api_settings = dict(lighting_settings or {})
    _lighting_global_config = dict(lighting_global or {})
    logger.info("API initialized with dependencies")


def _api_lighting_on() -> bool:
    """Return True if illumination was applied (caller should call _api_lighting_off_if)."""
    lc = lighting_controller
    if not lc or not lc.is_ready():
        return False
    if not _lighting_api_settings.get("use_for_api_capture", True):
        return False
    rgb = _lighting_api_settings.get("default_rgb", [255, 255, 255])
    r, g, b = (int(rgb[0]) & 0xFF, int(rgb[1]) & 0xFF, int(rgb[2]) & 0xFF)
    lc.fill(r, g, b)
    lc.show()
    time.sleep(float(_lighting_api_settings.get("settle_ms", 2.0)) / 1000.0)
    return True


def _api_lighting_off_if(applied: bool) -> None:
    if not applied or not lighting_controller or not lighting_controller.is_ready():
        return
    if not _lighting_api_settings.get("off_after_capture", True):
        return
    lighting_controller.off()


def _make_api_lighting_hooks() -> Tuple[Optional[Callable[[], None]], Optional[Callable[[], None]]]:
    state = {"applied": False}

    def before() -> None:
        state["applied"] = _api_lighting_on()

    def after() -> None:
        _api_lighting_off_if(state["applied"])

    if lighting_controller and lighting_controller.is_ready() and _lighting_api_settings.get(
        "use_for_api_capture", True
    ):
        return before, after
    return None, None


# ==================== PROGRAM ENDPOINTS ====================

@api.route('/programs', methods=['POST'])
@validate_json_request(required_fields=['name', 'config'])
def create_program():
    """
    POST /api/programs
    Body: {name, config}
    Returns: {id, message}
    Errors: 400 (validation), 409 (duplicate name), 500
    """
    try:
        data = request.get_json()
        
        # Create program
        program = program_manager.create_program(data)

        # Persist master image to disk if it was sent as raw base64
        cfg = data.get('config', {})
        _save_master_image_from_config(program['id'], cfg)

        _sync_program_owned_template(program['id'], program['name'], cfg)
        program = program_manager.get_program(program['id']) or program

        return jsonify(program), 201
        
    except ValueError as e:
        logger.warning(f"Program creation failed - validation error: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Program creation failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/programs', methods=['GET'])
def list_programs():
    """
    GET /api/programs
    Query params: ?active_only=true
    Returns: {programs: [...]}
    """
    try:
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        
        programs = program_manager.list_programs(active_only=active_only)
        
        return jsonify({'programs': programs}), 200
        
    except Exception as e:
        logger.error(f"List programs failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/programs/<int:program_id>', methods=['GET'])
def get_program(program_id):
    """
    GET /api/programs/:id
    Returns: {id, name, config, stats}
    Errors: 404
    """
    try:
        program = program_manager.get_program(program_id)
        
        if not program:
            return jsonify({'error': 'Program not found'}), 404
        
        return jsonify(program), 200
        
    except Exception as e:
        logger.error(f"Get program failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/programs/<int:program_id>', methods=['PUT'])
@validate_json_request()
def update_program(program_id):
    """
    PUT /api/programs/:id
    Body: {name?, config?}
    Returns: {message}
    """
    try:
        updates = request.get_json()

        program = program_manager.update_program(program_id, updates)

        # Persist master image to disk if a new base64 was sent with the update
        if 'config' in updates:
            cfg = updates.get('config', {})
            _save_master_image_from_config(program_id, cfg)
        if 'config' in updates or 'name' in updates:
            fresh = program_manager.get_program(program_id) or program
            cfg = updates.get('config') if 'config' in updates else (fresh.get('config') or {})
            _sync_program_owned_template(
                program_id,
                updates.get('name') or fresh['name'],
                cfg,
            )
            program = program_manager.get_program(program_id) or program

        return jsonify({
            'message': 'Program updated successfully',
            'program': program
        }), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400 if 'not found' not in str(e) else 404
    except Exception as e:
        logger.error(f"Update program failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/programs/<int:program_id>', methods=['DELETE'])
def delete_program(program_id):
    """
    DELETE /api/programs/:id
    Permanently removes the program, its tool template, DB rows, and storage files.
    Returns: {message}
    """
    try:
        if tool_template_manager:
            try:
                tool_template_manager.delete_program_template(program_id)
            except Exception as exc:
                logger.warning('Could not delete program-owned template: %s', exc)

        program_manager.delete_program(program_id)
        
        return jsonify({'message': 'Program deleted successfully'}), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Delete program failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ==================== TOOL TEMPLATE ENDPOINTS ====================

@api.route('/tool-templates', methods=['GET'])
def list_tool_templates():
    """GET /api/tool-templates — list saved tool configuration templates."""
    try:
        if not tool_template_manager:
            return jsonify({'error': 'Tool template manager not initialized'}), 503
        program_id = request.args.get('program_id', type=int)
        if program_id is not None:
            templates = tool_template_manager.list_templates_for_program_ui(
                configuring_program_id=program_id
            )
        else:
            templates = tool_template_manager.list_templates()
        return jsonify({'templates': templates}), 200
    except Exception as e:
        logger.error(f"List tool templates failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/tool-templates/<int:template_id>/for-program/<int:program_id>', methods=['GET'])
def get_tool_template_for_program(template_id, program_id):
    """
    GET /api/tool-templates/:id/for-program/:programId
    Returns template tools with ROIs scaled to the program master image size (preview / debug).
    """
    try:
        if not tool_template_manager:
            return jsonify({'error': 'Tool template manager not initialized'}), 503
        program = program_manager.get_program(program_id)
        if not program:
            return jsonify({'error': 'Program not found'}), 404
        master_path = resolve_master_image_path_for_engine(program)
        import cv2
        master_bgr = cv2.imread(master_path)
        if master_bgr is None:
            return jsonify({'error': 'Master image not readable'}), 400
        master_rgb = cv2.cvtColor(master_bgr, cv2.COLOR_BGR2RGB)
        from src.core.tool_roi import master_image_dimensions
        mw, mh = master_image_dimensions(master_rgb)
        tools = tool_template_manager.get_tools_for_master(template_id, mw, mh)
        template = tool_template_manager.get_template(template_id, include_image=False)
        return jsonify({
            'templateId': template_id,
            'templateName': template.get('name') if template else None,
            'programId': program_id,
            'masterSize': {'width': mw, 'height': mh},
            'tools': tools,
        }), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f'get_tool_template_for_program failed: {e}')
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/tool-templates/<int:template_id>', methods=['GET'])
def get_tool_template(template_id):
    """GET /api/tool-templates/:id — full template with tools and reference image."""
    try:
        if not tool_template_manager:
            return jsonify({'error': 'Tool template manager not initialized'}), 503
        include_image = request.args.get('include_image', 'true').lower() != 'false'
        template = tool_template_manager.get_template(template_id, include_image=include_image)
        if not template:
            return jsonify({'error': 'Template not found'}), 404
        return jsonify(template), 200
    except Exception as e:
        logger.error(f"Get tool template failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/tool-templates', methods=['POST'])
@validate_json_request(required_fields=['name', 'tools'])
def create_tool_template():
    """
    POST /api/tool-templates
    Body: { name, tools, description? }
    """
    try:
        if not tool_template_manager:
            return jsonify({'error': 'Tool template manager not initialized'}), 503
        data = request.get_json()
        template = tool_template_manager.create_template(
            name=data['name'],
            tools=data['tools'],
            description=data.get('description', ''),
            roi_space=data.get('roi_space'),
        )
        return jsonify({
            'message': 'Tool template saved successfully',
            'template': template,
        }), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Create tool template failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/programs/<int:program_id>/tool-template', methods=['PUT'])
@validate_json_request(required_fields=['tools'])
def upsert_program_tool_template(program_id):
    """
    PUT /api/programs/:id/tool-template
    Body: { tools, description?, roi_space? }
    Updates only this program's owned template (named like the program).
    """
    try:
        if not tool_template_manager or not program_manager:
            return jsonify({'error': 'Not initialized'}), 503
        program = program_manager.get_program(program_id)
        if not program:
            return jsonify({'error': 'Program not found'}), 404
        data = request.get_json()
        cfg = dict(program.get('config') or {})
        template = tool_template_manager.upsert_program_template(
            program_id=program_id,
            program_name=program['name'],
            tools=data['tools'],
            template_id_hint=cfg.get('toolTemplateId'),
            roi_space=data.get('roi_space') or cfg.get('toolsRoiSpace'),
            description=data.get('description', ''),
        )
        template_id = int(template['id'])
        cfg['toolTemplateId'] = template_id
        cfg['toolTemplateOwned'] = True
        if data.get('tools'):
            cfg['tools'] = data['tools']
        if data.get('roi_space'):
            cfg['toolsRoiSpace'] = data['roi_space']
        program_manager.update_program(program_id, {'config': cfg})
        return jsonify({
            'message': 'Program template saved',
            'template': template,
            'toolTemplateId': template_id,
        }), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f'upsert_program_tool_template failed: {e}\n{traceback.format_exc()}')
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/tool-templates/<int:template_id>', methods=['DELETE'])
def delete_tool_template(template_id):
    """DELETE /api/tool-templates/:id"""
    try:
        if not tool_template_manager:
            return jsonify({'error': 'Tool template manager not initialized'}), 503
        tool_template_manager.delete_template(template_id)
        return jsonify({'message': 'Template deleted successfully'}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Delete tool template failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ==================== MASTER IMAGE ENDPOINTS ====================

@api.route('/master-image', methods=['POST'])
@validate_file_upload(allowed_extensions=['jpg', 'jpeg', 'png'], max_size_mb=10)
def upload_master_image():
    """
    POST /api/master-image
    Content-Type: multipart/form-data
    File: image file
    Body: {programId}
    Returns: {path, quality_score}
    Validation: file type, size, quality
    
    NOTE: Uploaded images are automatically re-encoded with consistent quality
    parameters (lossless PNG) to ensure matching consistency with captured images.
    This is critical for accurate template matching during inspection.
    """
    try:
        # Get file
        file = request.files['file']
        
        # Get program ID from form data
        program_id = request.form.get('programId')
        if not program_id:
            return jsonify({'error': 'programId is required'}), 400
        
        try:
            program_id = int(program_id)
        except ValueError:
            return jsonify({'error': 'programId must be an integer'}), 400
        
        # Read image
        import numpy as np
        import cv2
        
        file_bytes = np.frombuffer(file.read(), np.uint8)
        image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        
        if image is None:
            return jsonify({'error': 'Invalid image file'}), 400
        
        # Convert BGR to RGB
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # Validate image quality
        quality = camera_controller.validate_image_quality(image_rgb)
        
        # Save image with consistent quality parameters (PNG lossless)
        # This re-encodes uploaded images to ensure consistency with camera captures
        image_path = program_manager.save_master_image(program_id, image_rgb)
        
        return jsonify({
            'path': image_path,
            'quality': quality,
            'message': 'Master image uploaded and re-encoded for quality consistency'
        }), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 404 if 'not found' in str(e) else 400
    except Exception as e:
        logger.error(f"Upload master image failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/master-image/<int:program_id>', methods=['GET'])
def get_master_image(program_id):
    """
    GET /api/master-image/:id
    Returns: {image: base64, format: "png" | "jpg"} (disk path is lossless PNG; legacy config may be JPEG)

    Primary path  : load from disk via master_image_path column.
    Fallback path : serve base64 embedded in config.masterImage (handles
                    programs created before the disk-save fix was deployed).
                    Also triggers a background save so future loads hit disk.
    """
    try:
        # ── Primary: load from disk ──────────────────────────────────────────
        image = program_manager.load_master_image(program_id)

        if image is not None:
            return jsonify(
                {
                    'image': numpy_to_base64(image, format=ARCHIVE_IMAGE_FORMAT),
                    'format': ARCHIVE_IMAGE_FORMAT,
                }
            ), 200

        # ── Fallback: base64 stored directly in config.masterImage ───────────
        program = program_manager.get_program(program_id)
        if not program:
            return jsonify({'error': 'Program not found'}), 404

        raw = (program.get('config') or {}).get('masterImage', '')
        if raw and _is_base64_image(raw):
            # Strip data-URI prefix so the client always gets plain base64
            b64 = raw.split(',', 1)[1] if ',' in raw else raw

            # Opportunistically save to disk for future loads
            _save_master_image_from_config(program_id, {'masterImage': raw})

            fmt = 'png' if b64.lstrip().startswith('iVBORw0KGgo') else 'jpg'
            return jsonify({'image': b64, 'format': fmt}), 200

        return jsonify({'error': 'Master image not found'}), 404

    except Exception as e:
        logger.error(f"Get master image failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ==================== CAMERA ENDPOINTS ====================

@api.route('/camera/info', methods=['GET'])
def camera_info():
    """
    GET /api/camera/info
    Returns hardware details: model, resolution, interface, sensor type, etc.
    """
    try:
        info = camera_controller.get_camera_info()
        return jsonify(info), 200
    except Exception as e:
        logger.error(f"Camera info failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/camera/capture', methods=['POST'])
def capture_image():
    """
    POST /api/camera/capture
    Body: {
        brightnessMode?:  'normal' | 'hdr' | 'highgain'   (preset),
        focusValue?:      0-100                             (logged, fixed-focus sensor ignores),
        exposureTime?:    µs integer                        (overrides preset),
        analogGain?:      float 1.0-16.0                   (overrides preset),
        digitalGain?:     float                            (optional digital multiplier)
    }
    Returns: {image: base64 (lossless PNG), format, quality: {...}, timestamp, cameraInfo}
    """
    try:
        data = request.get_json() or {}

        brightness_mode = data.get('brightnessMode', 'normal')
        focus_value     = int(data.get('focusValue', 50))
        exposure_time   = data.get('exposureTime', None)
        analog_gain     = data.get('analogGain', None)
        digital_gain    = data.get('digitalGain', None)

        if brightness_mode not in ['normal', 'hdr', 'highgain']:
            return jsonify({'error': 'Invalid brightness mode'}), 400
        if not (0 <= focus_value <= 100):
            return jsonify({'error': 'Focus value must be 0-100'}), 400

        applied = _api_lighting_on()
        try:
            image = camera_controller.capture_image(
                brightness_mode=brightness_mode,
                focus_value=focus_value,
                exposure_time_us=int(exposure_time) if exposure_time is not None else None,
                analog_gain=float(analog_gain) if analog_gain is not None else None,
                digital_gain=float(digital_gain) if digital_gain is not None else None,
            )
        finally:
            _api_lighting_off_if(applied)

        if image is None:
            return jsonify({'error': 'Failed to capture image'}), 500

        from src.utils.image_processing import ensure_native_capture_rgb

        image, _ = ensure_native_capture_rgb(image)

        quality       = camera_controller.validate_image_quality(image)
        image_base64  = numpy_to_base64(image, format=ARCHIVE_IMAGE_FORMAT)
        cam_info      = camera_controller.get_camera_info()
        from src.utils.image_processing import capture_dimensions_meta
        dims = capture_dimensions_meta(image)

        return jsonify({
            'image':      image_base64,
            'format':     ARCHIVE_IMAGE_FORMAT,
            'quality':    quality,
            'timestamp':  datetime.now().isoformat(),
            'cameraInfo': cam_info,
            'width':      dims['width'],
            'height':     dims['height'],
            'isNativeResolution': dims['isNativeResolution'],
            'nativeWidth': dims['nativeWidth'],
            'nativeHeight': dims['nativeHeight'],
        }), 200

    except Exception as e:
        logger.error(f"Capture image failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/camera/auto-optimize', methods=['POST'])
def auto_optimize_camera():
    """
    POST /api/camera/auto-optimize
    Optimises exposure/gain by testing presets; focus is fixed on the IMX296.
    Returns: {optimalBrightness, optimalFocus, brightnessScores, focusScore, fixedFocus, message}
    """
    try:
        logger.info("Starting camera auto-optimization...")

        before_l, after_l = _make_api_lighting_hooks()
        optimal_brightness, brightness_scores = camera_controller.auto_optimize_brightness(
            before_capture=before_l,
            after_capture=after_l,
        )
        optimal_focus, focus_score = camera_controller.auto_optimize_focus(
            before_capture=before_l,
            after_capture=after_l,
        )

        cam_info    = camera_controller.get_camera_info()
        fixed_focus = cam_info.get('focus_type', '').startswith('Fixed')

        logger.info(f"Auto-optimization complete: brightness={optimal_brightness}, focus={optimal_focus}")

        return jsonify({
            'optimalBrightness': optimal_brightness,
            'optimalFocus':      optimal_focus,
            'brightnessScores':  brightness_scores,
            'focusScore':        focus_score,
            'fixedFocus':        fixed_focus,
            'message':           'Camera optimization complete',
        }), 200

    except Exception as e:
        logger.error(f"Auto-optimization failed: {e}")
        return jsonify({'error': 'Auto-optimization failed'}), 500


@api.route('/camera/preview/start', methods=['POST'])
def start_preview():
    """Start live camera preview"""
    try:
        camera_controller.start_preview()
        
        return jsonify({'message': 'Preview started'}), 200
        
    except Exception as e:
        logger.error(f"Start preview failed: {e}")
        return jsonify({'error': 'Failed to start preview'}), 500


@api.route('/camera/preview/stop', methods=['POST'])
def stop_preview():
    """Stop live camera preview"""
    try:
        camera_controller.stop_preview()
        
        return jsonify({'message': 'Preview stopped'}), 200
        
    except Exception as e:
        logger.error(f"Stop preview failed: {e}")
        return jsonify({'error': 'Failed to stop preview'}), 500


# ==================== GPIO ENDPOINTS ====================

@api.route('/gpio/outputs', methods=['GET'])
def get_gpio_outputs():
    """Get current state of all GPIO outputs"""
    try:
        states = gpio_controller.get_all_states()
        
        return jsonify({'outputs': states}), 200
        
    except Exception as e:
        logger.error(f"Get GPIO outputs failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/gpio/outputs/<int:output_number>', methods=['POST'])
@validate_json_request(required_fields=['state'])
def set_gpio_output(output_number):
    """
    POST /api/gpio/outputs/:number
    Body: {state: true/false}
    """
    try:
        data = request.get_json()
        state = data.get('state')
        
        if not isinstance(state, bool):
            return jsonify({'error': 'state must be boolean'}), 400
        
        gpio_controller.set_output(output_number, state)
        
        return jsonify({
            'message': f'Output {output_number} set to {"HIGH" if state else "LOW"}'
        }), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Set GPIO output failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/gpio/write', methods=['POST'])
@validate_json_request(required_fields=['pin', 'value'])
def write_gpio():
    """
    POST /api/gpio/write
    Body: {pin: "OUT1", value: true/false}
    
    Compatible endpoint for Run page GPIO control.
    Converts pin name (OUT1-OUT8) to output number (1-8).
    """
    try:
        data = request.get_json()
        pin = data.get('pin')
        value = data.get('value')
        
        # Validate pin format
        if not isinstance(pin, str) or not pin.startswith('OUT'):
            return jsonify({'error': 'pin must be in format OUT1-OUT8'}), 400
        
        # Extract pin number
        try:
            pin_number = int(pin.replace('OUT', ''))
            if pin_number < 1 or pin_number > 8:
                return jsonify({'error': 'pin number must be 1-8'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid pin format'}), 400
        
        # Validate value
        if not isinstance(value, bool):
            return jsonify({'error': 'value must be boolean'}), 400
        
        # Set GPIO output
        gpio_controller.set_output(pin_number, value)
        
        logger.debug(f"GPIO {pin} set to {value}")
        
        return jsonify({
            'message': f'GPIO write successful',
            'pin': pin,
            'value': value
        }), 200
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"GPIO write failed: {e}")
        return jsonify({'error': 'GPIO write failed'}), 500


@api.route('/gpio/test', methods=['POST'])
def test_gpio_sequence():
    """Run GPIO test sequence"""
    try:
        gpio_controller.test_sequence()
        
        return jsonify({'message': 'GPIO test sequence complete'}), 200
        
    except Exception as e:
        logger.error(f"GPIO test failed: {e}")
        return jsonify({'error': 'Test sequence failed'}), 500


# ==================== INSPECTION LIGHTING (single GPIO or optional P9813) ====================


@api.route('/lighting/status', methods=['GET'])
def lighting_status():
    """Report active lighting driver and YAML defaults."""
    try:
        lc = lighting_controller
        ready = bool(lc and lc.is_ready())
        driver, pins = describe_lighting_controller(lc)
        return jsonify({
            'driver': driver,
            'ready': ready,
            'pins': pins,
            'settings': {k: v for k, v in _lighting_api_settings.items()},
        }), 200
    except Exception as e:
        logger.error(f"Lighting status failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/lighting/rgb', methods=['POST'])
def lighting_set_rgb():
    """
    POST /api/lighting/rgb
    Body: { r, g, b: 0-255 } — set colour; single-GPIO drivers map to intensity (max channel).
    Or: { pixels: [[r,g,b], ...] } — first pixel is used for single-GPIO; full chain for P9813.
    """
    try:
        if not lighting_controller or not lighting_controller.is_ready():
            return jsonify({'error': 'Lighting not configured or not ready'}), 503

        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({'error': 'JSON body required'}), 400
        pixels = data.get('pixels')
        if pixels is not None:
            if not isinstance(pixels, list):
                return jsonify({'error': 'pixels must be a list of [r,g,b]'}), 400
            tuples = []
            for i, p in enumerate(pixels):
                if not isinstance(p, (list, tuple)) or len(p) < 3:
                    return jsonify({'error': f'pixels[{i}] must be [r,g,b]'}), 400
                tuples.append((int(p[0]), int(p[1]), int(p[2])))
            lighting_controller.set_from_sequence(tuples)
        elif 'r' in data and 'g' in data and 'b' in data:
            r, g, b = int(data['r']), int(data['g']), int(data['b'])
            if not all(0 <= x <= 255 for x in (r, g, b)):
                return jsonify({'error': 'r,g,b must be 0-255'}), 400
            lighting_controller.fill(r, g, b)
        else:
            return jsonify({'error': 'Provide either pixels or r, g, b'}), 400

        lighting_controller.show()
        return jsonify({'message': 'Lighting updated'}), 200
    except Exception as e:
        logger.error(f"Lighting rgb failed: {e}")
        return jsonify({'error': str(e)}), 500


@api.route('/lighting/off', methods=['POST'])
def lighting_off():
    """Turn all chain LEDs off."""
    try:
        if not lighting_controller or not lighting_controller.is_ready():
            return jsonify({'error': 'Lighting not configured or not ready'}), 503
        lighting_controller.off()
        return jsonify({'message': 'Lighting off'}), 200
    except Exception as e:
        logger.error(f"Lighting off failed: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== INSPECTION HISTORY ENDPOINTS ====================

@api.route('/inspections', methods=['POST'])
def log_inspection():
    """
    POST /api/inspections
    Body: {
        program_id,
        status: "OK" | "NG",
        processing_time_ms,
        tool_results: [...],
        trigger_type?,
        image?,   # optional base64-encoded image (with or without data-URI prefix); stored as PNG (same encoding as master)
        notes?
    }
    Returns: {id, image_path, message}
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Request body required'}), 400

        program_id = data.get('program_id') or data.get('programId')
        status = data.get('status') or data.get('overall_status')
        processing_time_ms = data.get('processing_time_ms') or data.get('processingTime', 0)
        tool_results = data.get('tool_results') or data.get('toolResults', [])
        trigger_type = data.get('trigger_type') or data.get('triggerType', 'internal')
        image_b64 = data.get('image')
        notes = data.get('notes')

        if not program_id:
            return jsonify({'error': 'program_id is required'}), 400
        if status not in ('OK', 'NG'):
            return jsonify({'error': 'status must be OK or NG'}), 400

        # --- Save inspection image to disk (if provided), same PNG encoding as master ---
        image_path = None
        if image_b64:
            try:
                if ',' in image_b64:
                    image_b64 = image_b64.split(',', 1)[1]

                image_rgb = base64_to_numpy(image_b64)
                image_path = program_manager.save_inspection_snapshot(
                    int(program_id), status, image_rgb
                )
                logger.debug("Inspection snapshot saved (master-equivalent encoding): %s", image_path)
            except Exception as img_err:
                logger.warning(f"Failed to save inspection image: {img_err}")

        result_id = program_manager.db.log_inspection_result(
            program_id=int(program_id),
            status=status,
            processing_time_ms=float(processing_time_ms),
            tool_results=tool_results,
            trigger_type=trigger_type,
            image_path=image_path,
            notes=notes
        )

        return jsonify({
            'id': result_id,
            'image_path': image_path,
            'message': 'Inspection result saved'
        }), 201

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Log inspection failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/inspection/run-once', methods=['POST'])
def inspection_run_once():
    """
    POST /api/inspection/run-once
    Run one inspection on the device using InspectionEngine (lighting, GPIO, algorithms).
    Same core path as /api/remote/inspection/run-once but for the local UI (no remote API key).

    Body JSON:
      programId (required)
      triggerType (optional, default 'internal')
      includeImage (optional, default true)
      persist (optional, default true) — write inspection_results + snapshot when true
    """
    if db_manager is None:
        return jsonify({'error': 'Inspection API not fully initialized'}), 503

    try:
        data = request.get_json(silent=True) or {}
        program_id = data.get('programId') or data.get('program_id')
        if program_id is None:
            return jsonify({'error': 'programId is required'}), 400
        program_id = int(program_id)

        trigger_type = data.get('triggerType') or data.get('trigger_type') or 'internal'
        include_image = data.get('includeImage', data.get('include_image', True))
        persist = data.get('persist', True)

        require_hw = bool(current_app.config.get('SLAVE_REQUIRE_REAL_HARDWARE'))

        try:
            payload = run_inspection_once(
                program_manager=program_manager,
                camera_controller=camera_controller,
                gpio_controller=gpio_controller,
                lighting_controller=lighting_controller,
                lighting_global_config=_lighting_global_config,
                db_manager=db_manager,
                program_id=program_id,
                trigger_type=str(trigger_type),
                include_image=bool(include_image),
                persist_result=bool(persist),
                require_real_hardware=require_hw,
            )
        except ValueError as e:
            msg = str(e)
            code = 404 if 'not found' in msg.lower() else 400
            return jsonify({'error': msg}), code
        except RuntimeError as e:
            msg = str(e)
            if 'Hardware not ready' in msg or 'CSI camera unavailable' in msg:
                return jsonify({'error': 'Hardware not ready', 'detail': msg}), 503
            return jsonify({'error': 'Inspection failed', 'detail': msg}), 500

        return jsonify(payload), 200

    except Exception as e:
        logger.error('inspection run-once failed: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/inspection/run-with-template', methods=['POST'])
def inspection_run_with_template():
    """
    POST /api/inspection/run-with-template
    Run one inspection using a saved tool template on a program's registered master image.

    Body JSON:
      templateId (required)
      programId (required) — source of master image + camera/GPIO/output defaults
      triggerType, includeImage, persist (optional)
    """
    if db_manager is None or not tool_template_manager:
        return jsonify({'error': 'Inspection API not fully initialized'}), 503

    try:
        data = request.get_json(silent=True) or {}
        template_id = data.get('templateId') or data.get('template_id')
        program_id = data.get('programId') or data.get('program_id')
        if template_id is None:
            return jsonify({'error': 'templateId is required'}), 400
        if program_id is None:
            return jsonify({'error': 'programId is required (master image host program)'}), 400
        template_id = int(template_id)
        program_id = int(program_id)

        trigger_type = data.get('triggerType') or data.get('trigger_type') or 'internal'
        include_image = data.get('includeImage', data.get('include_image', True))
        persist = data.get('persist', True)
        require_hw = bool(current_app.config.get('SLAVE_REQUIRE_REAL_HARDWARE'))

        try:
            payload = run_inspection_with_template(
                program_manager=program_manager,
                tool_template_manager=tool_template_manager,
                camera_controller=camera_controller,
                gpio_controller=gpio_controller,
                lighting_controller=lighting_controller,
                lighting_global_config=_lighting_global_config,
                db_manager=db_manager,
                template_id=template_id,
                program_id=program_id,
                trigger_type=str(trigger_type),
                include_image=bool(include_image),
                persist_result=bool(persist),
                require_real_hardware=require_hw,
            )
        except ValueError as e:
            msg = str(e)
            code = 404 if 'not found' in msg.lower() else 400
            return jsonify({'error': msg}), code
        except RuntimeError as e:
            msg = str(e)
            if 'Hardware not ready' in msg or 'CSI camera unavailable' in msg:
                return jsonify({'error': 'Hardware not ready', 'detail': msg}), 503
            return jsonify({'error': 'Inspection failed', 'detail': msg}), 500

        return jsonify(payload), 200

    except Exception as e:
        logger.error('inspection run-with-template failed: %s\n%s', e, traceback.format_exc())
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/inspections/<int:program_id>/<int:result_id>/image', methods=['GET'])
def get_inspection_image(program_id, result_id):
    """
    GET /api/inspections/:program_id/:result_id/image
    Returns the saved inspection snapshot (PNG or legacy JPEG) for a specific result.
    """
    try:
        rec = program_manager.db.get_inspection_result_by_id(program_id, result_id)

        if not rec:
            return jsonify({'error': 'Inspection result not found'}), 404

        img_path = rec.get('image_path')
        if not img_path or not os.path.exists(img_path):
            return jsonify({'error': 'Image not found'}), 404

        mime, _ = mimetypes.guess_type(img_path)
        return send_file(img_path, mimetype=mime or 'application/octet-stream')

    except Exception as e:
        logger.error(f"Get inspection image failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/inspections/<int:program_id>', methods=['GET'])
def get_inspection_history(program_id):
    """
    GET /api/inspections/:program_id
    Query params: ?limit=100&status=OK|NG
    Returns: {history: [...], program_id, total}
    """
    try:
        limit = int(request.args.get('limit', 100))
        status_filter = request.args.get('status') or None

        if status_filter and status_filter not in ('OK', 'NG'):
            return jsonify({'error': 'status filter must be OK or NG'}), 400

        history = program_manager.db.get_inspection_history(
            program_id=program_id,
            limit=limit,
            status_filter=status_filter
        )

        return jsonify({
            'history': history,
            'program_id': program_id,
            'total': len(history)
        }), 200

    except Exception as e:
        logger.error(f"Get inspection history failed: {e}")
        return jsonify({'error': 'Internal server error'}), 500


# ==================== HEALTH CHECK ====================

@api.route('/health', methods=['GET'])
def health_check():
    """
    GET /api/health
    Returns: {status, camera, gpio, database, storage}
    """
    health_status = {
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'components': {}
    }
    
    # Check camera
    try:
        test_image = camera_controller.capture_image()
        health_status['components']['camera'] = 'ok' if test_image is not None else 'error'
    except Exception as cam_err:
        logger.warning('Health camera check failed: %s', cam_err)
        health_status['components']['camera'] = 'error'
    
    # Check GPIO
    try:
        gpio_controller.get_all_states()
        health_status['components']['gpio'] = 'ok'
    except Exception as gpio_err:
        logger.warning('Health GPIO check failed: %s', gpio_err)
        health_status['components']['gpio'] = 'error'
    
    # Check database
    try:
        program_manager.list_programs()
        health_status['components']['database'] = 'ok'
    except Exception as db_err:
        logger.warning('Health database check failed: %s', db_err)
        health_status['components']['database'] = 'error'
    
    # Check storage
    try:
        storage_ok = (
            os.path.exists(program_manager.master_images_path) and
            os.path.exists(program_manager.image_history_path)
        )
        health_status['components']['storage'] = 'ok' if storage_ok else 'error'
    except Exception as st_err:
        logger.warning('Health storage check failed: %s', st_err)
        health_status['components']['storage'] = 'error'
    
    # Set overall status
    if any(status == 'error' for status in health_status['components'].values()):
        health_status['status'] = 'degraded'

    # Explicit boolean for UIs that do not want to branch on string vs object shapes
    health_status['camera_ok'] = health_status['components'].get('camera') == 'ok'
    
    return jsonify(health_status), 200


# ==================== ERROR HANDLERS ====================

@api.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@api.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


@api.errorhandler(Exception)
def handle_exception(error):
    logger.error(f"Unhandled exception: {error}\n{traceback.format_exc()}")
    return jsonify({'error': 'An unexpected error occurred'}), 500

