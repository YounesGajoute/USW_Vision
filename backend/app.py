"""Vision Inspection System - Main Flask Application"""

import logging
import os
import sys
import yaml
from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

# Add backend directory to path
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND_DIR)
# Optional secrets / overrides (systemd also loads backend/.env via EnvironmentFile)
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

from src.api.routes import api, init_api
from src.api.remote_routes import remote_bp, init_remote_api
from src.api.backup_routes import backup_api, init_backup_api
from src.api.monitoring_routes import monitoring_api
from src.api.health import health_bp
from src.api.engineio_path_middleware import EngineIoPathNormalizeMiddleware
from src.api.websocket import socketio, init_websocket
from src.database.db_manager import DatabaseManager, set_db
from src.database.migration_manager import MigrationManager
from src.core.program_manager import ProgramManager
from src.core.tool_template_manager import ToolTemplateManager
from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController
from src.hardware.p9813_lighting import build_lighting_from_config, lighting_settings_from_yaml
from src.utils.logger import setup_logging, get_logger
from src.monitoring import (
    init_metrics_collector,
    init_performance_tracker,
    init_system_monitor,
    init_alert_manager
)


def load_config(config_path='config.yaml'):
    """Load configuration from YAML file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def create_app(config_path='config.yaml'):
    """
    Application factory pattern.
    Creates and configures the Flask application.
    """
    # Load configuration
    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)
    
    # Setup logging
    try:
        setup_logging(config.get('logging', {}))
        logger = get_logger('app')
        logger.info("=== Vision Inspection System Starting ===")
        logger.info(f"System: {config['system']['name']} v{config['system']['version']}")
    except Exception as e:
        print(f"Error setting up logging: {e}")
        sys.exit(1)
    
    # Initialize Flask app
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config.from_mapping(config)
    remote_cfg = config.get('remote') or {}
    app.config['REMOTE_API_KEY'] = (
        os.environ.get('VISION_REMOTE_API_KEY', remote_cfg.get('api_key') or '') or ''
    ).strip()

    slave_cfg = config.get('slave') or {}
    app.config['SLAVE_REQUIRE_REAL_HARDWARE'] = bool(slave_cfg.get('require_real_hardware', False))
    if os.environ.get('VISION_SLAVE_REQUIRE_HARDWARE', '').lower() in ('1', 'true', 'yes'):
        app.config['SLAVE_REQUIRE_REAL_HARDWARE'] = True
    app.config['SLAVE_REQUIRE_REMOTE_API_KEY'] = bool(slave_cfg.get('require_remote_api_key', False))
    if os.environ.get('VISION_SLAVE_REQUIRE_REMOTE_API_KEY', '').lower() in ('1', 'true', 'yes'):
        app.config['SLAVE_REQUIRE_REMOTE_API_KEY'] = True
    local_cfg = config.get('local') or {}
    app.config['LOCAL_API_KEY'] = (
        os.environ.get('VISION_LOCAL_API_KEY', local_cfg.get('api_key') or '') or ''
    ).strip()
    app.config['SLAVE_REQUIRE_LOCAL_API_KEY'] = bool(slave_cfg.get('require_local_api_key', False))
    app.config['SLAVE_REQUIRE_SOCKETIO_AUTH'] = bool(slave_cfg.get('require_socketio_auth', False))

    sock_mode = (os.environ.get('VISION_SOCKETIO_AUTH_MODE') or remote_cfg.get('socketio_auth') or 'none').lower()
    if sock_mode == 'inherit':
        app.config['REMOTE_SOCKETIO_AUTH_KEY'] = app.config['REMOTE_API_KEY']
    elif sock_mode == 'secondary':
        app.config['REMOTE_SOCKETIO_AUTH_KEY'] = (
            os.environ.get('VISION_SOCKETIO_AUTH_KEY', remote_cfg.get('socketio_key') or '') or ''
        ).strip()
    else:
        app.config['REMOTE_SOCKETIO_AUTH_KEY'] = ''

    cam_cfg_pre = config.get('camera', {}) or {}
    slave_req_hw = bool(slave_cfg.get('require_real_hardware', False))
    if 'allow_test_pattern' in cam_cfg_pre:
        camera_allow_test_pattern = bool(cam_cfg_pre['allow_test_pattern'])
    else:
        camera_allow_test_pattern = not slave_req_hw

    # Setup CORS
    cors_origins = config.get('api', {}).get('cors_origins', ['http://localhost:3000'])
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": cors_origins,
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
                "allow_headers": [
                    "Content-Type",
                    "Authorization",
                    "X-Vision-Remote-Key",
                    "X-Vision-Local-Key",
                ],
            }
        },
    )
    logger.info(f"CORS enabled for origins: {cors_origins}")
    
    # Initialize database
    try:
        logger.info("Initializing database...")
        db_manager = DatabaseManager(config['database']['path'])
        set_db(db_manager)
        logger.info("Database initialized successfully")
        
        # Initialize migration manager and apply pending migrations
        logger.info("Checking database migrations...")
        migration_manager = MigrationManager(config['database']['path'])
        
        # Check for pending migrations
        pending = migration_manager.list_pending_migrations()
        if pending:
            logger.info(f"Found {len(pending)} pending migration(s)")
            successful, failed = migration_manager.apply_all_pending(dry_run=False)
            if failed > 0:
                logger.error(f"Migration failed: {failed} migration(s) failed")
                logger.warning("Continuing with current schema version")
            else:
                logger.info(f"Successfully applied {successful} migration(s)")
        else:
            logger.info(f"Database schema up to date (version {migration_manager.get_current_version()})")
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        sys.exit(1)
    
    # Initialize hardware controllers
    try:
        logger.info("Initializing hardware controllers...")
        
        # Camera
        cam_cfg = config.get('camera', {}) or {}
        resolution = tuple(cam_cfg.get('resolution', [640, 480]))
        camera_device = cam_cfg.get('device', 0)
        isp_output_format = str(cam_cfg.get('isp_output_format', 'RGB161616') or 'RGB161616')
        camera_controller = CameraController(
            resolution=resolution,
            camera_device=camera_device,
            allow_test_pattern=camera_allow_test_pattern,
            isp_output_format=isp_output_format,
        )
        logger.info("Camera controller initialized")
        
        # GPIO
        gpio_pins = config.get('gpio', {}).get('outputs', [17, 18, 27, 22, 23, 24, 25, 8])
        gpio_controller = GPIOController(output_pins=gpio_pins)
        logger.info("GPIO controller initialized")

        lighting_cfg = config.get("lighting") or {}
        inspection_lighting = build_lighting_from_config(lighting_cfg)
        lighting_settings = lighting_settings_from_yaml(lighting_cfg)
        
    except Exception as e:
        if slave_req_hw:
            logger.error(
                "Hardware initialization failed and slave.require_real_hardware is true: %s",
                e,
            )
            sys.exit(1)
        logger.warning(f"Hardware initialization warning: {e}")
        logger.info("Continuing with simulated hardware for development")
        cam_cfg = config.get('camera', {}) or {}
        resolution = tuple(cam_cfg.get('resolution', [640, 480]))
        camera_device = cam_cfg.get('device', 0)
        isp_output_format = str(cam_cfg.get('isp_output_format', 'RGB161616') or 'RGB161616')
        camera_controller = CameraController(
            resolution=resolution,
            camera_device=camera_device,
            allow_test_pattern=camera_allow_test_pattern,
            isp_output_format=isp_output_format,
        )
        gpio_controller = GPIOController()
        lighting_cfg = config.get("lighting") or {}
        inspection_lighting = build_lighting_from_config(lighting_cfg)
        lighting_settings = lighting_settings_from_yaml(lighting_cfg)
    
    # Initialize program manager
    try:
        logger.info("Initializing program manager...")
        storage_config = config.get('storage', {})
        program_manager = ProgramManager(db_manager, storage_config)
        tool_template_manager = ToolTemplateManager(storage_config, program_manager)
        logger.info("Program manager initialized")
    except Exception as e:
        logger.error(f"Program manager initialization failed: {e}")
        sys.exit(1)
    
    # Initialize monitoring system
    try:
        logger.info("Initializing monitoring system...")
        
        # Metrics collector
        metrics_collector = init_metrics_collector(
            db_manager,
            buffer_size=1000,
            flush_interval=10
        )
        
        # Performance tracker
        performance_tracker = init_performance_tracker(metrics_collector)
        
        # System monitor (checks every 5 seconds)
        system_monitor = init_system_monitor(
            metrics_collector,
            interval=5
        )
        
        # Alert manager
        alert_manager = init_alert_manager(db_manager, metrics_collector)
        
        logger.info("Monitoring system initialized successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize monitoring system: {e}")
        logger.warning("Continuing without monitoring (graceful degradation)")
    
    # Shared camera for health checks (avoids a second Picamera2 open during /health/full)
    app.extensions['vision_camera_controller'] = camera_controller

    # Initialize API with dependencies
    init_api(
        program_manager,
        camera_controller,
        gpio_controller,
        lighting=inspection_lighting,
        lighting_settings=lighting_settings,
        db=db_manager,
        lighting_global=lighting_cfg,
        tool_templates=tool_template_manager,
    )
    init_remote_api(
        program_manager,
        camera_controller,
        gpio_controller,
        db_manager,
        lighting=inspection_lighting,
        lighting_global=lighting_cfg,
    )
    logger.info("API initialized")
    
    # Initialize backup API
    backup_storage_path = config.get('storage', {}).get('backups', './storage/backups')
    os.makedirs(backup_storage_path, exist_ok=True)
    init_backup_api(program_manager, db_manager, backup_storage_path)
    logger.info("Backup API initialized")
    
    # Register blueprints
    app.register_blueprint(api, url_prefix='/api')
    app.register_blueprint(remote_bp, url_prefix='/api/remote')
    app.register_blueprint(backup_api, url_prefix='/api/backup')
    app.register_blueprint(monitoring_api, url_prefix='/api/monitoring')
    app.register_blueprint(health_bp, url_prefix='/api')
    logger.info("API blueprints registered")
    
    # Socket.IO: allow browser/UI from listed origins; optional "*" for LAN (see config remote.socketio_cors)
    _sio_cors = remote_cfg.get('socketio_cors')
    if _sio_cors is None or _sio_cors == '':
        socketio_cors = cors_origins
    else:
        socketio_cors = _sio_cors
    socketio.init_app(
        app,
        cors_allowed_origins=socketio_cors,
        async_mode='threading',
        logger=False,
        engineio_logger=False
    )
    init_websocket(
        program_manager,
        camera_controller,
        db_manager,
        gpio_controller,
        lighting=inspection_lighting,
        lighting_global=lighting_cfg,
        lighting_settings=lighting_settings,
        tool_templates=tool_template_manager,
    )
    logger.info("WebSocket initialized")

    app.wsgi_app = EngineIoPathNormalizeMiddleware(app.wsgi_app)
    
    # Add health check route at root
    @app.route('/')
    def index():
        return {
            'name': config['system']['name'],
            'version': config['system']['version'],
            'status': 'running'
        }
    
    # Add cleanup handler
    @app.teardown_appcontext
    def cleanup(error=None):
        """Cleanup resources on shutdown."""
        if error:
            logger.error(f"Application error: {error}")
    
    logger.info("Application initialization complete")
    logger.info(f"API available at: http://{config['api']['host']}:{config['api']['port']}/api")
    
    return app


def _suppress_werkzeug_socketio_access_log() -> None:
    """Engine.IO polling spams GET/POST /socket.io; hide from Werkzeug access log."""

    class _SocketIoAccessFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            if '/socket.io/' in msg and ('GET ' in msg or 'POST ' in msg):
                return False
            return True

    logging.getLogger('werkzeug').addFilter(_SocketIoAccessFilter())


def main():
    """Main entry point."""
    # Get config path from environment or use default
    config_path = os.environ.get('CONFIG_PATH', 'config.yaml')
    
    # Create app
    app = create_app(config_path)
    _suppress_werkzeug_socketio_access_log()
    
    # Load config for server settings
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Get server settings
    host = config.get('api', {}).get('host', '0.0.0.0')
    port = config.get('api', {}).get('port', 5000)
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    logger = get_logger('app')
    logger.info(f"Starting server on {host}:{port} (debug={debug})")
    
    # Run with SocketIO
    try:
        socketio.run(
            app,
            host=host,
            port=port,
            debug=debug,
            use_reloader=debug,
            allow_unsafe_werkzeug=True  # Allow Werkzeug for development
        )
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

