"""
Production-ready Flask application with all enhancements.
This is the main application factory with complete error handling,
authentication, rate limiting, and monitoring.
"""

import os
import sys
import traceback
from flask import Flask, jsonify, g
from flask_cors import CORS

# Add backend directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Import configuration
from config.config import get_config

# Import core components
from src.database.db_manager import DatabaseManager, set_db
from src.core.program_manager import ProgramManager
from src.core.tool_template_manager import ToolTemplateManager
from src.hardware.camera import CameraController
from src.hardware.gpio_controller import GPIOController
from src.hardware.p9813_lighting import P9813Lighting, lighting_settings_from_yaml
from src.hardware.single_gpio_lighting import SingleGpioLighting

# Import API components
from src.api.routes import api, init_api
from src.api.remote_routes import remote_bp, init_remote_api
from src.api.auth_routes import auth_bp
from src.api.health import health_bp
from src.api.engineio_path_middleware import EngineIoPathNormalizeMiddleware
from src.api.websocket import socketio, init_websocket
from src.api.middleware import init_middleware
from src.api.rate_limiter import init_rate_limiter
from src.api.auth import init_auth_service

# Import logging
from src.utils.logging_config import setup_logging, get_logger

# Import exceptions
from src.utils.exceptions import (
    VisionSystemError, ValidationError, AuthenticationError,
    CameraError, InspectionError, DatabaseError, HardwareError,
    ImageProcessingError, RateLimitExceededError
)


def create_app(config_name=None):
    """
    Application factory pattern.
    Creates and configures the Flask application with all production features.
    
    Args:
        config_name: Configuration name (development, production, testing)
                     If None, uses FLASK_ENV environment variable
    
    Returns:
        Configured Flask application
    """
    # Get configuration
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')
    
    config = get_config(config_name)
    
    # Initialize Flask app
    app = Flask(__name__)
    app.config.from_object(config)
    app.config["REMOTE_API_KEY"] = os.getenv("VISION_REMOTE_API_KEY", "").strip()
    app.config["SLAVE_REQUIRE_REAL_HARDWARE"] = (
        os.getenv("VISION_SLAVE_REQUIRE_HARDWARE", "").lower() in ("1", "true", "yes")
    )
    app.config["SLAVE_REQUIRE_REMOTE_API_KEY"] = (
        os.getenv("VISION_SLAVE_REQUIRE_REMOTE_API_KEY", "").lower() in ("1", "true", "yes")
    )
    app.config["LOCAL_API_KEY"] = os.getenv("VISION_LOCAL_API_KEY", "").strip()
    app.config["SLAVE_REQUIRE_LOCAL_API_KEY"] = (
        os.getenv("VISION_SLAVE_REQUIRE_LOCAL_API_KEY", "").lower() in ("1", "true", "yes")
    )
    app.config["SLAVE_REQUIRE_SOCKETIO_AUTH"] = (
        os.getenv("VISION_SLAVE_REQUIRE_SOCKETIO_AUTH", "").lower() in ("1", "true", "yes")
    )

    _sio_mode = os.getenv("VISION_SOCKETIO_AUTH_MODE", "none").lower()
    if _sio_mode == "inherit":
        app.config["REMOTE_SOCKETIO_AUTH_KEY"] = app.config["REMOTE_API_KEY"]
    elif _sio_mode == "secondary":
        app.config["REMOTE_SOCKETIO_AUTH_KEY"] = os.getenv("VISION_SOCKETIO_AUTH_KEY", "").strip()
    else:
        app.config["REMOTE_SOCKETIO_AUTH_KEY"] = ""

    _slave_hw = app.config["SLAVE_REQUIRE_REAL_HARDWARE"]
    _atp_env = os.getenv("VISION_ALLOW_CAMERA_TEST_PATTERN")
    if _atp_env is not None:
        camera_allow_test_pattern = _atp_env.lower() in ("1", "true", "yes")
    else:
        camera_allow_test_pattern = not _slave_hw

    # Initialize configuration
    config.init_app(app)
    
    # Setup logging
    logger = setup_logging(app.config)
    logger.info("=== Vision Inspection System Starting ===")
    logger.info(f"Environment: {config_name}")
    logger.info(f"Debug mode: {app.config['DEBUG']}")
    
    # Setup CORS (Next and browsers use /api/*; production also serves /api/v1/*)
    _cors_opts = {
        "origins": config.CORS_ORIGINS,
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Request-ID", "X-Vision-Remote-Key"],
        "expose_headers": ["X-Request-ID"],
        "supports_credentials": True,
    }
    CORS(
        app,
        resources={
            r"/api/*": _cors_opts,
            r"/api/v1/*": _cors_opts,
        },
    )
    logger.info(f"CORS enabled for origins: {config.CORS_ORIGINS}")
    
    # Initialize middleware
    init_middleware(app, config)
    logger.info("Middleware initialized")
    
    # Initialize database
    try:
        logger.info("Initializing database...")
        db_path = config.DATABASE_URL.replace('sqlite:///', '')
        db_manager = DatabaseManager(db_path)
        set_db(db_manager)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        if config_name == 'production':
            sys.exit(1)
        else:
            logger.warning("Continuing without database in development mode")
    
    # Initialize authentication service
    try:
        auth_service = init_auth_service(db_manager, config)
        logger.info("Authentication service initialized")
    except Exception as e:
        logger.error(f"Auth service initialization failed: {e}")
        if config_name == 'production':
            sys.exit(1)
    
    # Initialize hardware controllers
    try:
        logger.info("Initializing hardware controllers...")
        
        # Camera
        camera_controller = CameraController(
            resolution=config.CAMERA_RESOLUTION,
            camera_device=config.CAMERA_DEVICE,
            allow_test_pattern=camera_allow_test_pattern,
            isp_output_format=config.CAMERA_ISP_FORMAT,
        )
        logger.info("Camera controller initialized")
        
        # GPIO
        gpio_controller = GPIOController(output_pins=config.GPIO_PINS)
        logger.info("GPIO controller initialized")

        lighting_global_env = {}
        inspection_lighting = None
        _lighting_common = {
            "during_capture": os.getenv("LIGHTING_DURING_CAPTURE", "true").lower()
            == "true",
            "settle_ms": float(os.getenv("LIGHTING_SETTLE_MS", "2")),
            "off_after_capture": os.getenv("LIGHTING_OFF_AFTER", "true").lower()
            == "true",
            "use_for_api_capture": os.getenv("LIGHTING_USE_FOR_API", "true").lower()
            == "true",
            "default_rgb": [
                int(x.strip())
                for x in os.getenv("LIGHTING_DEFAULT_RGB", "255,255,255").split(",")
            ][:3],
        }
        if os.getenv("LIGHTING_GPIO_ENABLED", "").lower() in ("1", "true", "yes"):
            try:
                inspection_lighting = SingleGpioLighting(
                    pin=int(os.getenv("LIGHTING_GPIO_PIN", "12")),
                    pwm=os.getenv("LIGHTING_GPIO_PWM", "true").lower() == "true",
                    pwm_frequency=int(os.getenv("LIGHTING_GPIO_PWM_FREQ", "1000")),
                    active_high=os.getenv("LIGHTING_GPIO_ACTIVE_HIGH", "true").lower()
                    == "true",
                    solid_at_full=os.getenv("LIGHTING_GPIO_SOLID_AT_FULL", "true").lower()
                    == "true",
                )
                lighting_global_env = dict(_lighting_common)
            except Exception as le:
                logger.warning("Single-GPIO lighting init failed: %s", le)
        elif os.getenv("LIGHTING_P9813_ENABLED", "").lower() in ("1", "true", "yes"):
            try:
                inspection_lighting = P9813Lighting(
                    clock_pin=int(os.getenv("LIGHTING_P9813_CLK", "19")),
                    data_pin=int(os.getenv("LIGHTING_P9813_DATA", "16")),
                    num_leds=int(os.getenv("LIGHTING_P9813_NUM_LEDS", "1")),
                )
                lighting_global_env = dict(_lighting_common)
            except Exception as le:
                logger.warning("P9813 lighting init failed: %s", le)
        lighting_settings = lighting_settings_from_yaml(lighting_global_env)
        
    except Exception as e:
        if _slave_hw:
            logger.error(
                "Hardware initialization failed and VISION_SLAVE_REQUIRE_HARDWARE is set: %s",
                e,
            )
            sys.exit(1)
        logger.warning(f"Hardware initialization warning: {e}")
        logger.info("Continuing with simulated hardware")
        camera_controller = CameraController(
            resolution=config.CAMERA_RESOLUTION,
            camera_device=config.CAMERA_DEVICE,
            allow_test_pattern=camera_allow_test_pattern,
            isp_output_format=config.CAMERA_ISP_FORMAT,
        )
        gpio_controller = GPIOController()
        lighting_global_env = {}
        inspection_lighting = None
        lighting_settings = lighting_settings_from_yaml({})
    
    # Initialize program manager
    try:
        logger.info("Initializing program manager...")
        storage_config = {
            'master_images': config.STORAGE_MASTER_IMAGES,
            'image_history': config.STORAGE_INSPECTION_IMAGES,
            'inspection_history': config.STORAGE_INSPECTION_IMAGES,
            'backups': config.STORAGE_BACKUP
        }
        program_manager = ProgramManager(db_manager, storage_config)
        tool_template_manager = ToolTemplateManager(storage_config, program_manager)
        logger.info("Program manager initialized")
    except Exception as e:
        logger.error(f"Program manager initialization failed: {e}")
        if config_name == 'production':
            sys.exit(1)

    # Shared camera for health checks (avoids opening a second Picamera2 instance)
    app.extensions['vision_camera_controller'] = camera_controller
    
    # Initialize API with dependencies
    init_api(
        program_manager,
        camera_controller,
        gpio_controller,
        lighting=inspection_lighting,
        lighting_settings=lighting_settings,
        db=db_manager,
        lighting_global=lighting_global_env,
        tool_templates=tool_template_manager,
    )
    init_remote_api(
        program_manager,
        camera_controller,
        gpio_controller,
        db_manager,
        lighting=inspection_lighting,
        lighting_global=lighting_global_env,
    )
    logger.info("API initialized")
    
    # Register blueprints — duplicate /api mount so Next (default /api) and /api/v1 clients both work
    app.register_blueprint(api, url_prefix='/api/v1')
    app.register_blueprint(api, url_prefix='/api', name='apiCompat')
    app.register_blueprint(remote_bp, url_prefix='/api/v1/remote')
    app.register_blueprint(remote_bp, url_prefix='/api/remote', name='remoteCompat')
    app.register_blueprint(auth_bp, url_prefix='/api/v1')
    app.register_blueprint(auth_bp, url_prefix='/api', name='authCompat')
    app.register_blueprint(health_bp, url_prefix='/api/v1')
    app.register_blueprint(health_bp, url_prefix='/api', name='healthCompat')
    logger.info("API blueprints registered")
    
    # Initialize rate limiting
    init_rate_limiter(app, config)
    
    # Initialize SocketIO (set VISION_SOCKETIO_CORS=* for Pi master clients on LAN)
    _sio = os.getenv("VISION_SOCKETIO_CORS")
    socketio_cors = (_sio.strip() if _sio and _sio.strip() else None) or config.CORS_ORIGINS
    socketio.init_app(
        app,
        cors_allowed_origins=socketio_cors,
        async_mode='eventlet',
        logger=False,
        engineio_logger=False
    )
    init_websocket(
        program_manager,
        camera_controller,
        db_manager,
        gpio_controller,
        lighting=inspection_lighting,
        lighting_global=lighting_global_env,
        lighting_settings=lighting_settings,
        tool_templates=tool_template_manager,
    )
    logger.info("WebSocket initialized")

    app.wsgi_app = EngineIoPathNormalizeMiddleware(app.wsgi_app)
    
    # ==================== GLOBAL ERROR HANDLERS ====================
    
    @app.errorhandler(ValidationError)
    def handle_validation_error(error):
        """Handle validation errors."""
        logger.warning(f"Validation error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message,
            'details': error.details
        }), 400
    
    @app.errorhandler(AuthenticationError)
    def handle_authentication_error(error):
        """Handle authentication errors."""
        logger.warning(f"Authentication error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message
        }), 401
    
    @app.errorhandler(DatabaseError)
    def handle_database_error(error):
        """Handle database errors."""
        logger.error(f"Database error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': 'Database operation failed',
            'details': error.details if app.debug else {}
        }), 500
    
    @app.errorhandler(CameraError)
    def handle_camera_error(error):
        """Handle camera errors."""
        logger.error(f"Camera error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message,
            'details': error.details if app.debug else {}
        }), 503
    
    @app.errorhandler(InspectionError)
    def handle_inspection_error(error):
        """Handle inspection errors."""
        logger.error(f"Inspection error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message,
            'details': error.details if app.debug else {}
        }), 500
    
    @app.errorhandler(HardwareError)
    def handle_hardware_error(error):
        """Handle hardware errors."""
        logger.error(f"Hardware error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message
        }), 503
    
    @app.errorhandler(ImageProcessingError)
    def handle_image_processing_error(error):
        """Handle image processing errors."""
        logger.error(f"Image processing error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message
        }), 500
    
    @app.errorhandler(VisionSystemError)
    def handle_vision_system_error(error):
        """Handle generic vision system errors."""
        logger.error(f"System error: {error.message}")
        return jsonify({
            'success': False,
            'error': error.error_code,
            'message': error.message,
            'details': error.details if app.debug else {}
        }), 500
    
    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 errors."""
        return jsonify({
            'success': False,
            'error': 'NOT_FOUND',
            'message': 'Resource not found'
        }), 404
    
    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        """Handle 405 errors."""
        return jsonify({
            'success': False,
            'error': 'METHOD_NOT_ALLOWED',
            'message': 'Method not allowed for this endpoint'
        }), 405
    
    @app.errorhandler(500)
    def handle_internal_error(error):
        """Handle 500 errors."""
        logger.error(f"Internal server error: {error}")
        return jsonify({
            'success': False,
            'error': 'INTERNAL_SERVER_ERROR',
            'message': 'An internal error occurred'
        }), 500
    
    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        """Handle all uncaught exceptions."""
        logger.error(f"Unhandled exception: {error}\n{traceback.format_exc()}")
        
        # In production, don't expose internal details
        if app.debug:
            return jsonify({
                'success': False,
                'error': 'UNEXPECTED_ERROR',
                'message': str(error),
                'traceback': traceback.format_exc()
            }), 500
        else:
            return jsonify({
                'success': False,
                'error': 'UNEXPECTED_ERROR',
                'message': 'An unexpected error occurred'
            }), 500
    
    # ==================== ROOT ENDPOINTS ====================
    
    @app.route('/')
    def index():
        """Root endpoint with API information."""
        return jsonify({
            'name': config.APP_NAME,
            'version': config.APP_VERSION,
            'status': 'running',
            'api_version': 'v1',
            'endpoints': {
                'health': '/api/health',
                'health_full': '/api/health/full',
                'health_legacy_v1': '/api/v1/health',
                'api_docs': '/api/v1/docs',
                'programs': '/api/programs',
                'authentication': '/api/auth'
            }
        })
    
    @app.route('/api/v1/docs')
    def api_docs():
        """API documentation endpoint."""
        return jsonify({
            'api_version': 'v1',
            'base_url': '/api',
            'base_url_alt': '/api/v1',
            'authentication': {
                'type': 'JWT Bearer Token',
                'header': 'Authorization: Bearer <token>',
                'endpoints': {
                    'login': 'POST /api/auth/login',
                    'refresh': 'POST /api/auth/refresh',
                    'logout': 'POST /api/auth/logout'
                }
            },
            'endpoints': {
                'programs': {
                    'list': 'GET /api/programs',
                    'create': 'POST /api/programs',
                    'get': 'GET /api/programs/{id}',
                    'update': 'PUT /api/programs/{id}',
                    'delete': 'DELETE /api/programs/{id}'
                },
                'camera': {
                    'capture': 'POST /api/camera/capture',
                    'preview_start': 'POST /api/camera/preview/start',
                    'preview_stop': 'POST /api/camera/preview/stop'
                },
                'health': {
                    'status': 'GET /api/health',
                    'full': 'GET /api/health/full',
                    'ready': 'GET /api/health/ready',
                    'live': 'GET /api/health/live'
                }
            },
            'websocket': {
                'url': '/socket.io',
                'events': {
                    'start_inspection': 'Start inspection loop',
                    'stop_inspection': 'Stop inspection loop',
                    'subscribe_live_feed': 'Subscribe to camera feed',
                    'unsubscribe_live_feed': 'Unsubscribe from camera feed'
                }
            }
        })
    
    # ==================== APPLICATION LIFECYCLE ====================
    
    @app.before_request
    def before_request():
        """Before request hook."""
        # Request ID and timing are handled by middleware
        pass
    
    @app.after_request
    def after_request(response):
        """After request hook."""
        # Add custom headers
        response.headers['X-API-Version'] = 'v1'
        return response
    
    @app.teardown_appcontext
    def cleanup(error=None):
        """Cleanup resources on shutdown."""
        if error:
            logger.error(f"Application error: {error}")
    
    # Log application readiness
    logger.info("Application initialization complete")
    logger.info(f"API available at: http://{config.API_HOST}:{config.API_PORT}/api (and /api/v1)")
    
    return app


def main():
    """Main entry point for running the application."""
    # Get config
    config_name = os.getenv('FLASK_ENV', 'development')
    config = get_config(config_name)
    
    # Create app
    app = create_app(config_name)
    
    # Get logger
    logger = get_logger('app')
    logger.info(f"Starting server on {config.API_HOST}:{config.API_PORT}")
    
    # Run with SocketIO
    try:
        socketio.run(
            app,
            host=config.API_HOST,
            port=config.API_PORT,
            debug=app.config['DEBUG'],
            use_reloader=app.config['DEBUG']
        )
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
