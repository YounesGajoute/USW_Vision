"""
Comprehensive health check and monitoring endpoints.
Provides system status, metrics, and diagnostics.
"""

import os
import psutil
import time
from datetime import datetime
from flask import Blueprint, current_app, has_app_context, jsonify, request
from typing import Dict, Any

from src.utils.logger import get_logger

logger = get_logger('health')

# Blueprint url_prefix is set at register_blueprint time in app factories.
health_bp = Blueprint('health', __name__)


def check_database() -> Dict[str, Any]:
    """Check database connectivity and health."""
    try:
        from src.database.db_manager import get_db
        db = get_db()
        
        # Try a simple query
        start_time = time.time()
        programs = db.list_programs()
        query_time = (time.time() - start_time) * 1000  # ms
        
        return {
            'status': 'healthy',
            'message': 'Database connection OK',
            'query_time_ms': round(query_time, 2),
            'program_count': len(programs)
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            'status': 'unhealthy',
            'message': f'Database error: {str(e)}',
            'error': str(e)
        }


def check_camera(probe_capture: bool = False) -> Dict[str, Any]:
    """Check camera availability and status.

    When probe_capture is False (default), use metadata only — no frame grab (avoids
    racing with inspection / live feed). Pass probe_capture=True or ?probe=capture on
    /health/full for a real capture test.
    """
    try:
        # Prefer the app's shared CameraController to avoid a second libcamera open
        # (which races with live preview / inspection and often fails with "camera busy").
        if has_app_context():
            cam = current_app.extensions.get('vision_camera_controller')
            if cam is not None:
                cam_info = cam.get_camera_info() if hasattr(cam, "get_camera_info") else {}
                simulated = bool(cam_info.get("simulated"))
                if not probe_capture:
                    if getattr(cam, "camera", None) is None and not simulated:
                        return {
                            'status': 'degraded',
                            'message': (
                                'CSI camera unavailable (Picamera2 not loaded or sensor failed).'
                            ),
                            'simulated': True,
                            'probe': 'metadata',
                        }
                    return {
                        'status': 'healthy',
                        'message': 'Camera controller ready (no capture probe)',
                        'simulated': simulated,
                        'probe': 'metadata',
                    }
                if getattr(cam, "camera", None) is None and not simulated:
                    return {
                        'status': 'degraded',
                        'message': (
                            'CSI camera unavailable (Picamera2 not loaded or sensor failed). '
                            'Install python3-libcamera/python3-picamera2 on Pi or use a venv with '
                            '--system-site-packages.'
                        ),
                        'simulated': True,
                    }
                start_time = time.time()
                try:
                    image = cam.capture_image()
                except Exception as cap_err:
                    logger.warning('Shared camera capture in health check failed: %s', cap_err)
                    return {
                        'status': 'degraded',
                        'message': f'Camera capture failed: {cap_err}',
                        'simulated': simulated,
                    }
                capture_time = (time.time() - start_time) * 1000  # ms
                if image is not None:
                    return {
                        'status': 'healthy',
                        'message': 'Camera operational (shared controller)',
                        'capture_time_ms': round(capture_time, 2),
                        'resolution': f"{image.shape[1]}x{image.shape[0]}",
                        'simulated': simulated,
                    }
                return {
                    'status': 'unhealthy',
                    'message': 'Camera capture returned no frame',
                    'simulated': simulated,
                }

        # No app context (e.g. isolated script): do not open a second camera by default
        return {
            'status': 'degraded',
            'message': 'Camera health skipped (no Flask app context or shared camera)',
            'simulated': True,
        }

    except Exception as e:
        logger.error(f"Camera health check failed: {e}")
        return {
            'status': 'unhealthy',
            'message': f'Camera error: {str(e)}',
            'error': str(e)
        }


def check_gpio() -> Dict[str, Any]:
    """Check GPIO availability."""
    try:
        from src.hardware.gpio_controller import GPIOController
        
        gpio = GPIOController()
        states = gpio.get_all_states()
        
        return {
            'status': 'healthy',
            'message': 'GPIO operational',
            'output_count': len(states)
        }
    except Exception as e:
        logger.error(f"GPIO health check failed: {e}")
        return {
            'status': 'degraded',
            'message': f'GPIO not available: {str(e)}',
            'error': str(e)
        }


def check_storage() -> Dict[str, Any]:
    """Check storage availability and space."""
    try:
        from config.config import get_config
        config = get_config()
        
        paths_to_check = [
            config.STORAGE_MASTER_IMAGES,
            config.STORAGE_INSPECTION_IMAGES,
            config.STORAGE_BACKUP
        ]
        
        all_exist = all(os.path.exists(path) for path in paths_to_check)
        
        # Check disk space
        disk_usage = psutil.disk_usage('/')
        free_gb = disk_usage.free / (1024 ** 3)
        total_gb = disk_usage.total / (1024 ** 3)
        used_percent = disk_usage.percent
        
        if not all_exist:
            return {
                'status': 'degraded',
                'message': 'Some storage directories missing',
                'disk_free_gb': round(free_gb, 2),
                'disk_used_percent': used_percent
            }
        
        if used_percent > 90:
            status = 'degraded'
            message = 'Disk space critically low'
        elif used_percent > 80:
            status = 'degraded'
            message = 'Disk space running low'
        else:
            status = 'healthy'
            message = 'Storage OK'
        
        return {
            'status': status,
            'message': message,
            'disk_free_gb': round(free_gb, 2),
            'disk_total_gb': round(total_gb, 2),
            'disk_used_percent': used_percent
        }
        
    except Exception as e:
        logger.error(f"Storage health check failed: {e}")
        return {
            'status': 'unknown',
            'message': f'Storage check error: {str(e)}',
            'error': str(e)
        }


def get_system_metrics() -> Dict[str, Any]:
    """Get system performance metrics."""
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count()
        
        # Memory
        memory = psutil.virtual_memory()
        memory_used_gb = memory.used / (1024 ** 3)
        memory_total_gb = memory.total / (1024 ** 3)
        memory_percent = memory.percent
        
        # Process info
        process = psutil.Process(os.getpid())
        process_memory = process.memory_info().rss / (1024 ** 2)  # MB
        process_cpu = process.cpu_percent(interval=0.1)
        
        # Uptime
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime_seconds = (datetime.now() - boot_time).total_seconds()
        
        return {
            'cpu': {
                'usage_percent': cpu_percent,
                'cores': cpu_count
            },
            'memory': {
                'used_gb': round(memory_used_gb, 2),
                'total_gb': round(memory_total_gb, 2),
                'used_percent': memory_percent
            },
            'process': {
                'memory_mb': round(process_memory, 2),
                'cpu_percent': process_cpu,
                'pid': os.getpid()
            },
            'uptime_hours': round(uptime_seconds / 3600, 2)
        }
    except Exception as e:
        logger.error(f"Failed to get system metrics: {e}")
        return {}


@health_bp.route('/health/full', methods=['GET'])
def health_check_full():
    """
    GET .../health/full
    Returns comprehensive system health status (metrics, all components).
    
    Response includes:
    - Overall status (healthy, degraded, unhealthy)
    - Component statuses (database, camera, gpio, storage)
    - System metrics (CPU, memory, disk)
    - Timestamp
    """
    start_time = time.time()
    
    probe_capture = request.args.get('probe') == 'capture'

    # Check all components
    components = {
        'database': check_database(),
        'camera': check_camera(probe_capture=probe_capture),
        'gpio': check_gpio(),
        'storage': check_storage()
    }
    
    # Get system metrics
    metrics = get_system_metrics()
    
    # Determine overall status
    statuses = [comp['status'] for comp in components.values()]
    
    if any(status == 'unhealthy' for status in statuses):
        overall_status = 'unhealthy'
    elif any(status == 'degraded' for status in statuses):
        overall_status = 'degraded'
    else:
        overall_status = 'healthy'
    
    check_duration = (time.time() - start_time) * 1000  # ms
    
    response = {
        'status': overall_status,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'components': components,
        'metrics': metrics,
        'check_duration_ms': round(check_duration, 2)
    }
    
    # Return appropriate HTTP status code
    http_status = 200 if overall_status == 'healthy' else 503
    
    return jsonify(response), http_status


@health_bp.route('/health/ready', methods=['GET'])
def readiness_check():
    """
    GET /api/health/ready (mirrored at /api/v1/health/ready when mounted in production)
    Kubernetes-style readiness probe.
    Returns 200 if service is ready to accept requests.
    """
    try:
        # Check critical components only
        db_status = check_database()
        
        if db_status['status'] == 'unhealthy':
            return jsonify({
                'ready': False,
                'reason': 'Database not available'
            }), 503
        
        return jsonify({
            'ready': True,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }), 200
        
    except Exception as e:
        return jsonify({
            'ready': False,
            'reason': str(e)
        }), 503


@health_bp.route('/health/live', methods=['GET'])
def liveness_check():
    """
    GET /api/health/live (mirrored at /api/v1/health/live)
    Kubernetes-style liveness probe.
    Returns 200 if service is alive (even if degraded).
    """
    return jsonify({
        'alive': True,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }), 200


@health_bp.route('/metrics', methods=['GET'])
def metrics():
    """
    GET /api/metrics (mirrored at /api/v1/metrics)
    Returns Prometheus-style metrics.
    
    Note: For production, consider using prometheus_client library.
    """
    try:
        metrics_data = get_system_metrics()
        
        # Get database stats
        from src.database.db_manager import get_db
        db = get_db()
        programs = db.list_programs()
        
        # Build Prometheus-style text format
        lines = [
            '# HELP vision_cpu_usage CPU usage percentage',
            '# TYPE vision_cpu_usage gauge',
            f'vision_cpu_usage {metrics_data.get("cpu", {}).get("usage_percent", 0)}',
            '',
            '# HELP vision_memory_usage Memory usage percentage',
            '# TYPE vision_memory_usage gauge',
            f'vision_memory_usage {metrics_data.get("memory", {}).get("used_percent", 0)}',
            '',
            '# HELP vision_programs_total Total number of programs',
            '# TYPE vision_programs_total gauge',
            f'vision_programs_total {len(programs)}',
            '',
            '# HELP vision_uptime_hours System uptime in hours',
            '# TYPE vision_uptime_hours gauge',
            f'vision_uptime_hours {metrics_data.get("uptime_hours", 0)}',
            ''
        ]
        
        return '\n'.join(lines), 200, {'Content-Type': 'text/plain; charset=utf-8'}
        
    except Exception as e:
        logger.error(f"Failed to generate metrics: {e}")
        return str(e), 500
