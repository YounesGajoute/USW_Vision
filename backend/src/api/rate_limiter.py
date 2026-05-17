"""
Rate limiting configuration for API endpoints.
Uses Flask-Limiter to prevent abuse and ensure fair usage.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import g
from typing import Callable

from src.utils.logger import get_logger

logger = get_logger('rate_limiter')

# Initialize limiter (will be configured by app factory)
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",  # Can be redis:// in production
    strategy="fixed-window"
)


def get_user_identifier() -> str:
    """
    Get identifier for rate limiting.
    Uses user ID if authenticated, otherwise IP address.
    """
    if hasattr(g, 'user_id'):
        return f"user:{g.user_id}"
    return f"ip:{get_remote_address()}"


def init_rate_limiter(app, config):
    """
    Initialize rate limiter with application and configuration.
    
    Args:
        app: Flask application
        config: Configuration object
    """
    if not config.RATELIMIT_ENABLED:
        logger.info("Rate limiting disabled")
        return
    
    # Configure limiter
    limiter.init_app(app)
    
    # Set storage URI
    if hasattr(config, 'RATELIMIT_STORAGE_URL'):
        limiter.storage_uri = config.RATELIMIT_STORAGE_URL
    
    logger.info(f"Rate limiting enabled - Default: {config.RATELIMIT_DEFAULT}")
    
    # Register error handler
    @app.errorhandler(429)
    def ratelimit_handler(e):
        return {
            'success': False,
            'error': 'RATE_LIMIT_EXCEEDED',
            'message': 'Too many requests. Please try again later.',
            'retry_after': e.description
        }, 429


# Predefined rate limit decorators for common use cases

def rate_limit_auth(f: Callable) -> Callable:
    """Apply authentication endpoint rate limit."""
    from config.config import get_config
    config = get_config()
    
    if config.RATELIMIT_ENABLED:
        return limiter.limit(config.RATELIMIT_AUTH)(f)
    return f


def rate_limit_api(f: Callable) -> Callable:
    """Apply default API rate limit."""
    from config.config import get_config
    config = get_config()
    
    if config.RATELIMIT_ENABLED:
        return limiter.limit(config.RATELIMIT_DEFAULT)(f)
    return f


def rate_limit_inspection(f: Callable) -> Callable:
    """Apply inspection endpoint rate limit."""
    from config.config import get_config
    config = get_config()
    
    if config.RATELIMIT_ENABLED:
        return limiter.limit(config.RATELIMIT_INSPECTION)(f)
    return f


def rate_limit_custom(limit: str):
    """
    Apply custom rate limit.
    
    Args:
        limit: Rate limit string (e.g., "10 per minute", "100 per hour")
    
    Usage:
        @rate_limit_custom("5 per minute")
        def expensive_operation():
            ...
    """
    def decorator(f: Callable) -> Callable:
        from config.config import get_config
        config = get_config()
        
        if config.RATELIMIT_ENABLED:
            return limiter.limit(limit)(f)
        return f
    
    return decorator


def exempt_from_rate_limit(f: Callable) -> Callable:
    """Exempt endpoint from rate limiting."""
    return limiter.exempt(f)
