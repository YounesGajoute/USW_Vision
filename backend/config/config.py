"""
Configuration classes for different environments.
Loads settings from environment variables with fallback defaults.
"""

import os
from typing import List


class Config:
    """Base configuration with common settings."""
    
    # Application
    APP_NAME = "Vision Inspection System"
    APP_VERSION = "1.0.0"
    
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Security
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', os.getenv('SECRET_KEY', 'dev-jwt-secret'))
    JWT_ACCESS_TOKEN_EXPIRES = int(os.getenv('SESSION_TIMEOUT', 3600))  # 1 hour
    JWT_REFRESH_TOKEN_EXPIRES = int(os.getenv('REFRESH_TOKEN_TIMEOUT', 604800))  # 7 days
    
    # Database
    DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./database/vision.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    
    # API
    API_HOST = os.getenv('API_HOST', '0.0.0.0')
    API_PORT = int(os.getenv('API_PORT', 5000))
    API_VERSION = os.getenv('API_VERSION', 'v1')
    
    # CORS
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')
    
    # Rate Limiting
    RATELIMIT_ENABLED = os.getenv('RATE_LIMIT_ENABLED', 'True').lower() == 'true'
    RATELIMIT_DEFAULT = os.getenv('RATE_LIMIT_DEFAULT', '100 per minute')
    RATELIMIT_AUTH = os.getenv('RATE_LIMIT_AUTH', '5 per minute')
    RATELIMIT_INSPECTION = os.getenv('RATE_LIMIT_INSPECTION', '10 per minute')
    RATELIMIT_STORAGE_URL = DATABASE_URL  # Use same database for rate limit storage
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', './logs/app.log')
    LOG_JSON_FORMAT = os.getenv('LOG_JSON_FORMAT', 'False').lower() == 'true'
    LOG_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', 10485760))  # 10MB
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', 30))
    
    # Camera
    CAMERA_DEVICE = int(os.getenv('CAMERA_DEVICE', 1))  # Picamera2 CSI camera index (see rpicam-still --list-cameras)
    CAMERA_RESOLUTION = (
        int(os.getenv('CAMERA_RESOLUTION_WIDTH', 1456)),
        int(os.getenv('CAMERA_RESOLUTION_HEIGHT', 1088))
    )
    CAMERA_ISP_FORMAT = os.getenv('CAMERA_ISP_FORMAT', 'RGB161616')
    CAMERA_FPS = int(os.getenv('CAMERA_FPS', 30))
    CAMERA_SIMULATED = os.getenv('CAMERA_SIMULATED', 'False').lower() == 'true'
    
    # GPIO
    GPIO_PINS = [int(pin.strip()) for pin in os.getenv('GPIO_PINS', '17,18,27,22,23,24,25,8').split(',')]
    
    # Storage
    STORAGE_MASTER_IMAGES = os.getenv('STORAGE_MASTER_IMAGES', './storage/master_images')
    STORAGE_INSPECTION_IMAGES = os.getenv('STORAGE_INSPECTION_IMAGES', './storage/image_history')
    STORAGE_BACKUP = os.getenv('STORAGE_BACKUP', './storage/backups')
    
    # Monitoring
    ENABLE_METRICS = os.getenv('ENABLE_METRICS', 'False').lower() == 'true'
    SENTRY_DSN = os.getenv('SENTRY_DSN', '')
    
    # Email Alerts
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    ALERT_EMAIL = os.getenv('ALERT_EMAIL', '')
    
    # Performance
    WORKERS = int(os.getenv('WORKERS', 4))
    WORKER_TIMEOUT = int(os.getenv('WORKER_TIMEOUT', 120))
    
    @classmethod
    def init_app(cls, app):
        """Initialize application with configuration."""
        # Create required directories
        for directory in [
            cls.STORAGE_MASTER_IMAGES,
            cls.STORAGE_INSPECTION_IMAGES,
            cls.STORAGE_BACKUP,
            os.path.dirname(cls.LOG_FILE),
            os.path.dirname(cls.DATABASE_URL.replace('sqlite:///', ''))
        ]:
            if directory and not directory.startswith('postgresql://'):
                os.makedirs(directory, exist_ok=True)


class DevelopmentConfig(Config):
    """Development environment configuration."""
    
    DEBUG = True
    TESTING = False
    
    # More verbose logging in development
    LOG_LEVEL = 'DEBUG'
    SQLALCHEMY_ECHO = True
    
    # Disable rate limiting in development
    RATELIMIT_ENABLED = False
    
    # Allow simulated hardware
    CAMERA_SIMULATED = True


class ProductionConfig(Config):
    """Production environment configuration."""
    
    DEBUG = False
    TESTING = False
    
    # Stricter settings for production
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_JSON_FORMAT = True  # JSON logs for production
    
    # Ensure secrets are set
    @classmethod
    def init_app(cls, app):
        super().init_app(app)
        
        # Validate critical configuration
        if cls.SECRET_KEY == 'dev-secret-key-change-in-production':
            raise ValueError("SECRET_KEY must be set in production!")
        
        if cls.JWT_SECRET_KEY == 'dev-jwt-secret':
            raise ValueError("JWT_SECRET_KEY must be set in production!")
        
        # Initialize Sentry if configured
        if cls.SENTRY_DSN:
            try:
                import sentry_sdk
                from sentry_sdk.integrations.flask import FlaskIntegration
                
                sentry_sdk.init(
                    dsn=cls.SENTRY_DSN,
                    integrations=[FlaskIntegration()],
                    environment='production'
                )
            except ImportError:
                app.logger.warning("Sentry SDK not installed. Error reporting disabled.")


class TestingConfig(Config):
    """Testing environment configuration."""
    
    DEBUG = True
    TESTING = True
    
    # Use in-memory database for tests
    DATABASE_URL = 'sqlite:///:memory:'
    
    # Disable rate limiting in tests
    RATELIMIT_ENABLED = False
    
    # Use simulated hardware
    CAMERA_SIMULATED = True
    
    # Faster token expiry for testing
    JWT_ACCESS_TOKEN_EXPIRES = 300  # 5 minutes


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config(env: str = None) -> Config:
    """
    Get configuration for specified environment.
    
    Args:
        env: Environment name (development, production, testing)
             If None, uses FLASK_ENV environment variable
    
    Returns:
        Configuration class
    """
    if env is None:
        env = os.getenv('FLASK_ENV', 'development')
    
    return config.get(env, config['default'])
