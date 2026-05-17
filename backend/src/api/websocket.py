"""WebSocket handlers for real-time communication"""

from flask import request
from flask_socketio import SocketIO, emit, disconnect
from threading import Thread, Event
import time
import traceback
from typing import Any, Dict, Optional

from src.core.inspection_engine import InspectionEngine
from src.core.inspection_runner import (
    build_engine_config_for_program,
    build_engine_config_for_template_run,
)
from src.hardware.camera import CameraController
from src.core.program_manager import ProgramManager
from src.database.db_manager import DatabaseManager
from src.utils.image_processing import ARCHIVE_IMAGE_FORMAT, numpy_to_base64
from src.utils.image_quality import analyze_image_quality_rgb
from src.utils.logger import get_logger

logger = get_logger('websocket')

# Initialize SocketIO (will be configured by app factory)
socketio = SocketIO()

# Global instances
program_manager: Optional[ProgramManager] = None
camera_controller: Optional[CameraController] = None
gpio_controller: Optional = None  # GPIOController
db_manager: Optional[DatabaseManager] = None
tool_template_manager: Optional[Any] = None
lighting_controller: Optional[Any] = None
lighting_global_config: Dict[str, Any] = {}

# Active sessions
active_inspections: Dict[str, Dict] = {}
active_feeds: Dict[str, Dict] = {}


def init_websocket(
    pm: ProgramManager,
    cam: CameraController,
    db: DatabaseManager,
    gpio=None,
    lighting=None,
    lighting_global: Optional[Dict[str, Any]] = None,
    tool_templates=None,
):
    """Initialize WebSocket with dependencies."""
    global program_manager, camera_controller, gpio_controller, db_manager
    global tool_template_manager, lighting_controller, lighting_global_config
    program_manager = pm
    camera_controller = cam
    gpio_controller = gpio
    db_manager = db
    tool_template_manager = tool_templates
    lighting_controller = lighting
    lighting_global_config = dict(lighting_global or {})
    logger.info("WebSocket initialized with dependencies")


# ==================== CONNECTION HANDLERS ====================

@socketio.on("connect")
def handle_connect(auth=None):
    """Client connected; optional auth.remoteKey when REMOTE_SOCKETIO_AUTH_KEY is configured."""
    from flask import current_app

    import secrets

    if current_app.config.get("SLAVE_REQUIRE_SOCKETIO_AUTH"):
        expected_prod = (current_app.config.get("REMOTE_SOCKETIO_AUTH_KEY") or "").strip()
        if not expected_prod:
            logger.warning(
                "Socket.IO connect rejected (sid=%s): SLAVE_REQUIRE_SOCKETIO_AUTH but no key configured",
                request.sid,
            )
            return False

    expected = (current_app.config.get("REMOTE_SOCKETIO_AUTH_KEY") or "").strip()
    if expected:
        token = None
        if isinstance(auth, dict):
            token = auth.get("remoteKey") or auth.get("token")
        if not token or not secrets.compare_digest(str(token).strip(), expected):
            logger.warning(
                "Socket.IO connect rejected (sid=%s): missing or invalid remoteKey in auth",
                request.sid,
            )
            return False

    logger.info(f"Client connected: {request.sid}")
    emit(
        "connection_status",
        {"status": "connected", "message": "Connected to vision inspection system"},
    )


@socketio.on('disconnect')
def handle_disconnect():
    """Client disconnected"""
    session_id = request.sid
    
    logger.info(f"Client disconnected: {session_id}")
    
    # Stop any active inspection for this session
    if session_id in active_inspections:
        active_inspections[session_id]['stop_flag'].set()
        del active_inspections[session_id]
    
    # Stop any active feed for this session
    if session_id in active_feeds:
        active_feeds[session_id]['stop_flag'].set()
        del active_feeds[session_id]


# ==================== INSPECTION HANDLERS ====================

@socketio.on('start_inspection')
def start_inspection(data):
    """
    Client sends: {programId, continuous: true/false}
    Start inspection loop
    Emit: inspection_result events
    """
    session_id = request.sid
    
    try:
        program_id = data.get('programId')
        continuous = data.get('continuous', True)
        
        if not program_id:
            emit('error', {'message': 'programId is required'})
            return

        from flask import current_app

        if current_app.config.get("SLAVE_REQUIRE_REAL_HARDWARE"):
            if camera_controller.get_camera_info().get("simulated"):
                emit(
                    "error",
                    {
                        "code": "NO_CAMERA",
                        "message": "CSI camera not available; cannot run inspection in production slave mode",
                    },
                )
                return

        # Load program
        program = program_manager.get_program(program_id)
        if not program:
            emit('error', {'message': f'Program {program_id} not found'})
            return

        template_id = data.get('templateId')
        try:
            if template_id is not None:
                if tool_template_manager is None:
                    emit('error', {'message': 'Tool template manager not initialized'})
                    return
                engine_config = build_engine_config_for_template_run(
                    program, int(template_id), tool_template_manager
                )
            else:
                engine_config = build_engine_config_for_program(program)
        except ValueError as ve:
            emit('error', {'message': str(ve)})
            return
        
        # Check if already running
        if session_id in active_inspections:
            emit('error', {'message': 'Inspection already running for this session'})
            return
        
        logger.info(f"Starting inspection for program {program_id} (session: {session_id})")
        
        # Create stop flag
        stop_flag = Event()
        
        # Store session info
        active_inspections[session_id] = {
            'program_id': program_id,
            'stop_flag': stop_flag,
            'thread': None
        }
        
        # Start inspection thread
        if continuous:
            thread = Thread(
                target=inspection_loop,
                args=(program_id, session_id, stop_flag, engine_config)
            )
            thread.daemon = True
            thread.start()
            active_inspections[session_id]['thread'] = thread
            
            emit('inspection_started', {
                'programId': program_id,
                'programName': program['name'],
                'continuous': True
            })
        else:
            # Single inspection
            thread = Thread(
                target=single_inspection,
                args=(program_id, session_id, engine_config)
            )
            thread.daemon = True
            thread.start()
            
            emit('inspection_started', {
                'programId': program_id,
                'programName': program['name'],
                'continuous': False
            })
        
    except Exception as e:
        logger.error(f"Start inspection failed: {e}\n{traceback.format_exc()}")
        emit('error', {'message': f'Failed to start inspection: {str(e)}'})


@socketio.on('stop_inspection')
def stop_inspection():
    """Stop active inspection"""
    session_id = request.sid
    
    try:
        if session_id not in active_inspections:
            emit('warning', {'message': 'No active inspection to stop'})
            return
        
        logger.info(f"Stopping inspection (session: {session_id})")
        
        # Set stop flag
        active_inspections[session_id]['stop_flag'].set()
        
        # Wait briefly for thread to stop
        time.sleep(0.5)
        
        # Clean up
        if session_id in active_inspections:
            del active_inspections[session_id]
        
        emit('inspection_stopped', {'message': 'Inspection stopped'})
        
    except Exception as e:
        logger.error(f"Stop inspection failed: {e}")
        emit('error', {'message': f'Failed to stop inspection: {str(e)}'})


def inspection_loop(program_id: int, session_id: str, stop_flag: Event, engine_config: Dict):
    """
    Continuous inspection loop.
    Runs in background thread.
    """
    try:
        # Create inspection engine with shared hardware controllers
        engine = InspectionEngine(
            engine_config,
            camera=camera_controller,
            gpio=gpio_controller,
            lighting=lighting_controller,
            lighting_global=lighting_global_config,
        )
        
        trigger_interval = engine_config.get('triggerInterval', 1000)
        
        inspection_count = 0
        
        while not stop_flag.is_set():
            try:
                # Run inspection cycle
                status, tool_results, processing_time, image = engine.run_inspection_cycle()
                
                inspection_count += 1

                image_path = None
                if image is not None:
                    try:
                        image_path = program_manager.save_inspection_snapshot(
                            program_id, status, image
                        )
                    except Exception as save_err:
                        logger.warning("Failed to save inspection snapshot: %s", save_err)
                
                # Log to database
                db_manager.log_inspection_result(
                    program_id=program_id,
                    status=status,
                    processing_time_ms=processing_time,
                    tool_results=tool_results,
                    trigger_type=engine_config.get('triggerType', 'internal'),
                    image_path=image_path,
                )
                
                # Full-resolution capture for review / persistence (same pixels as inspection).
                image_base64 = numpy_to_base64(image, format=ARCHIVE_IMAGE_FORMAT)
                
                # Emit result
                socketio.emit('inspection_result', {
                    'programId': program_id,
                    'status': status,
                    'toolResults': tool_results,
                    'processingTime': processing_time,
                    'inspectionCount': inspection_count,
                    'image': image_base64,
                    'format': ARCHIVE_IMAGE_FORMAT,
                    'timestamp': time.time()
                }, room=session_id)
                
                logger.debug(f"Inspection {inspection_count}: {status} ({processing_time:.1f}ms)")
                
            except Exception as e:
                logger.error(f"Inspection cycle failed: {e}")
                socketio.emit('error', {
                    'message': f'Inspection cycle failed: {str(e)}'
                }, room=session_id)
            
            # Wait for next trigger
            trigger_interval_sec = trigger_interval / 1000.0
            stop_flag.wait(trigger_interval_sec)
        
        logger.info(f"Inspection loop ended. Total inspections: {inspection_count}")
        
    except Exception as e:
        logger.error(f"Inspection loop crashed: {e}\n{traceback.format_exc()}")
        socketio.emit('error', {
            'message': f'Inspection loop crashed: {str(e)}'
        }, room=session_id)
    finally:
        # Cleanup
        if 'engine' in locals():
            engine.cleanup()


def single_inspection(program_id: int, session_id: str, engine_config: Dict):
    """Run a single inspection."""
    try:
        # Create inspection engine with shared hardware controllers
        engine = InspectionEngine(
            engine_config,
            camera=camera_controller,
            gpio=gpio_controller,
            lighting=lighting_controller,
            lighting_global=lighting_global_config,
        )
        
        # Run inspection
        status, tool_results, processing_time, image = engine.run_inspection_cycle()

        image_path = None
        if image is not None:
            try:
                image_path = program_manager.save_inspection_snapshot(
                    program_id, status, image
                )
            except Exception as save_err:
                logger.warning("Failed to save inspection snapshot: %s", save_err)
        
        # Log to database
        db_manager.log_inspection_result(
            program_id=program_id,
            status=status,
            processing_time_ms=processing_time,
            tool_results=tool_results,
            trigger_type='manual',
            image_path=image_path,
        )
        
        # Convert image to base64 (lossless PNG — same as disk snapshots)
        image_base64 = numpy_to_base64(image, format=ARCHIVE_IMAGE_FORMAT)
        
        # Emit result
        socketio.emit('inspection_result', {
            'programId': program_id,
            'status': status,
            'toolResults': tool_results,
            'processingTime': processing_time,
            'image': image_base64,
            'format': ARCHIVE_IMAGE_FORMAT,
            'timestamp': time.time(),
            'single': True
        }, room=session_id)
        
        socketio.emit('inspection_complete', {
            'message': 'Single inspection complete'
        }, room=session_id)
        
        engine.cleanup()
        
    except Exception as e:
        logger.error(f"Single inspection failed: {e}\n{traceback.format_exc()}")
        socketio.emit('error', {
            'message': f'Inspection failed: {str(e)}'
        }, room=session_id)


# ==================== LIVE FEED HANDLERS ====================

@socketio.on('subscribe_live_feed')
def subscribe_live_feed(data=None):
    """
    Start sending live camera frames.
    Emit: live_frame events (base64 lossless PNG, same encoding as capture/inspection)
    """
    session_id = request.sid
    
    try:
        fps = (data or {}).get('fps', 15)
        full_resolution = bool((data or {}).get('fullResolution', False))
        # Full-resolution PNG frames are large; cap FPS for configuration preview.
        if full_resolution:
            fps = max(1, min(6, fps))
        else:
            fps = max(1, min(60, fps))

        if session_id in active_feeds:
            emit('warning', {'message': 'Live feed already active'})
            return

        from flask import current_app

        if current_app.config.get("SLAVE_REQUIRE_REAL_HARDWARE"):
            if camera_controller.get_camera_info().get("simulated"):
                emit(
                    "error",
                    {
                        "code": "NO_CAMERA",
                        "message": "CSI camera not available; live feed disabled in production slave mode",
                    },
                )
                return

        logger.info(f"Starting live feed (session: {session_id}, fps: {fps})")
        
        # Create stop flag
        stop_flag = Event()
        
        # Store session info
        active_feeds[session_id] = {
            'stop_flag': stop_flag,
            'fps': fps,
            'full_resolution': full_resolution,
            'thread': None
        }
        
        # Start feed thread
        thread = Thread(
            target=live_feed_loop,
            args=(session_id, stop_flag, fps, full_resolution)
        )
        thread.daemon = True
        thread.start()
        active_feeds[session_id]['thread'] = thread
        
        emit('live_feed_started', {'fps': fps, 'fullResolution': full_resolution})
        
    except Exception as e:
        logger.error(f"Subscribe live feed failed: {e}")
        emit('error', {'message': f'Failed to start live feed: {str(e)}'})


@socketio.on('unsubscribe_live_feed')
def unsubscribe_live_feed():
    """Stop live feed"""
    session_id = request.sid
    
    try:
        if session_id not in active_feeds:
            emit('warning', {'message': 'No active live feed'})
            return
        
        logger.info(f"Stopping live feed (session: {session_id})")
        
        # Set stop flag
        active_feeds[session_id]['stop_flag'].set()
        
        # Wait briefly
        time.sleep(0.2)
        
        # Clean up
        if session_id in active_feeds:
            del active_feeds[session_id]
        
        emit('live_feed_stopped', {'message': 'Live feed stopped'})
        
    except Exception as e:
        logger.error(f"Unsubscribe live feed failed: {e}")
        emit('error', {'message': f'Failed to stop live feed: {str(e)}'})


def live_feed_loop(session_id: str, stop_flag: Event, fps: int, full_resolution: bool = False):
    """
    Continuously capture frames from the IMX296 and stream them to the client.

    Default: downscale to 640×480 for bandwidth. With full_resolution=True, stream
    native sensor size (up to 1456×1088) as lossless PNG — same quality as /camera/capture.
    Quality metrics always use a downscaled copy so scoring stays comparable.
    """
    import cv2
    import numpy as np

    from src.utils.image_processing import NATIVE_CAPTURE_H, NATIVE_CAPTURE_W

    PREVIEW_W, PREVIEW_H = 640, 480
    QUALITY_EVERY_N_FRAMES = 4

    try:
        frame_interval = 1.0 / fps
        frame_count = 0
        last_ts = time.time()
        last_quality: Dict[str, float] = {
            "brightness": 0.0,
            "luminance_median": 0.0,
            "contrast": 0.0,
            "sharpness": 0.0,
            "sharpness_index": 0.0,
            "exposure": 0.0,
            "information": 0.0,
            "score": 0.0,
        }

        consecutive_failures = 0
        while not stop_flag.is_set():
            t0 = time.time()
            try:
                # First frame applies Picamera2 controls + settle; rest are fast grab-only.
                stream_fast = frame_count > 0
                frame = camera_controller.capture_image(
                    for_stream=stream_fast,
                    live_preview=True,
                )

                if frame is None:
                    if not getattr(camera_controller, "allow_test_pattern", True):
                        logger.error(
                            "Live feed: capture failed and test pattern disabled — stopping stream"
                        )
                        socketio.emit(
                            "error",
                            {
                                "code": "CAMERA_UNAVAILABLE",
                                "message": "Camera capture failed; stream stopped (production mode)",
                            },
                            room=session_id,
                        )
                        socketio.emit(
                            "live_feed_stopped",
                            {"message": "Live feed stopped (camera unavailable)", "code": "CAMERA_UNAVAILABLE"},
                            room=session_id,
                        )
                        break
                    logger.warning(
                        "Live feed: capture_image returned None; using test pattern (dev only)"
                    )
                    frame = camera_controller._generate_test_pattern()
                    consecutive_failures += 1
                    if consecutive_failures >= 5:
                        socketio.emit(
                            "warning",
                            {
                                "message": "Camera repeatedly failed; streaming test pattern. Check logs and hardware.",
                            },
                            room=session_id,
                        )
                        consecutive_failures = 0
                else:
                    consecutive_failures = 0

                if frame is not None:
                    h, w = int(frame.shape[0]), int(frame.shape[1])
                    stream_frame = frame
                    if not full_resolution and (w, h) != (PREVIEW_W, PREVIEW_H):
                        stream_frame = cv2.resize(
                            frame, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA
                        )
                    sh, sw = stream_frame.shape[:2]

                    # Lossless PNG — same encoding as /camera/capture and inspection archives.
                    frame_base64 = numpy_to_base64(stream_frame, format=ARCHIVE_IMAGE_FORMAT)

                    if frame_count % QUALITY_EVERY_N_FRAMES == 0:
                        try:
                            q_src = frame
                            if full_resolution and (w, h) != (PREVIEW_W, PREVIEW_H):
                                q_src = cv2.resize(
                                    frame, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA
                                )
                            last_quality = analyze_image_quality_rgb(q_src)
                        except Exception:
                            logger.debug("live_frame quality analysis skipped", exc_info=True)

                    now = time.time()
                    elapsed = now - last_ts
                    live_fps = round(1.0 / elapsed, 1) if elapsed > 0 else fps
                    last_ts = now

                    socketio.emit('live_frame', {
                        'image':       frame_base64,
                        'format':      ARCHIVE_IMAGE_FORMAT,
                        'frameNumber': frame_count,
                        'timestamp':   now,
                        'fps':         live_fps,
                        'latencyMs':   round((now - t0) * 1000, 1),
                        'quality': {k: round(float(v), 1) for k, v in last_quality.items()},
                        'resolution':  f'{sw}×{sh}',
                        'fullResolution': full_resolution,
                        'isNativeResolution': (
                            full_resolution and sw == NATIVE_CAPTURE_W and sh == NATIVE_CAPTURE_H
                        ),
                    }, room=session_id)

                    frame_count += 1

            except Exception as e:
                logger.error(f"Live feed frame error: {e}")

            # Pace to requested FPS
            elapsed_this_frame = time.time() - t0
            wait = max(0.0, frame_interval - elapsed_this_frame)
            stop_flag.wait(wait)

        logger.info(f"Live feed ended (session={session_id}). Total frames: {frame_count}")

    except Exception as e:
        logger.error(f"Live feed loop crashed: {e}\n{traceback.format_exc()}")
    finally:
        active_feeds.pop(session_id, None)


# ==================== SYSTEM STATUS ====================

@socketio.on('request_system_status')
def request_system_status():
    """Request current system status"""
    try:
        status = {
            'activeInspections': len(active_inspections),
            'activeLiveFeeds': len(active_feeds),
            'timestamp': time.time()
        }
        
        emit('system_status', status)
        
    except Exception as e:
        logger.error(f"Get system status failed: {e}")
        emit('error', {'message': 'Failed to get system status'})


# ==================== ERROR HANDLER ====================

@socketio.on_error_default
def default_error_handler(e):
    """Default error handler"""
    logger.error(f"WebSocket error: {e}\n{traceback.format_exc()}")
    emit('error', {'message': 'An error occurred'})

