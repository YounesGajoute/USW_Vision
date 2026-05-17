"""
Middleware for request processing, logging, and authentication.
"""

import time
import uuid
from functools import wraps
from flask import request, g, jsonify
from typing import Callable
import jwt

from src.utils.logger import get_logger
from src.utils.exceptions import (
    TokenExpiredError, TokenInvalidError, InsufficientPermissionsError,
    AuthenticationError
)

logger = get_logger('middleware')


def request_id_middleware(app):
    """
    Add unique request ID to each request for tracing.
    Stored in Flask's g object and response headers.
    """
    @app.before_request
    def before_request():
        # Generate or extract request ID
        request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        g.request_id = request_id
        g.request_start_time = time.time()
        
        # Log incoming request
        logger.info(
            f"[{request_id}] {request.method} {request.path}",
            extra={
                'request_id': request_id,
                'method': request.method,
                'path': request.path,
                'remote_addr': request.remote_addr,
                'user_agent': request.user_agent.string if request.user_agent else None
            }
        )
    
    @app.after_request
    def after_request(response):
        # Add request ID to response headers
        if hasattr(g, 'request_id'):
            response.headers['X-Request-ID'] = g.request_id
        
        # Log response
        if hasattr(g, 'request_start_time'):
            duration = (time.time() - g.request_start_time) * 1000  # ms
            
            logger.info(
                f"[{g.request_id}] {response.status_code} - {duration:.2f}ms",
                extra={
                    'request_id': g.request_id,
                    'status_code': response.status_code,
                    'duration_ms': duration
                }
            )
        
        return response


def cors_middleware(app, allowed_origins: list):
    """
    Configure CORS headers for cross-origin requests.
    
    Args:
        app: Flask application
        allowed_origins: List of allowed origin URLs
    """
    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get('Origin')
        
        # Check if origin is allowed
        if origin in allowed_origins or '*' in allowed_origins:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Request-ID'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Max-Age'] = '3600'
        
        return response
    
    @app.route('/', methods=['OPTIONS'])
    @app.route('/<path:path>', methods=['OPTIONS'])
    def handle_options(path=None):
        """Handle preflight OPTIONS requests."""
        return '', 204


def response_compression_middleware(app):
    """Enable response compression for JSON responses."""
    try:
        from flask_compress import Compress
        Compress(app)
        logger.info("Response compression enabled")
    except ImportError:
        logger.warning("flask-compress not installed. Compression disabled.")


def security_headers_middleware(app):
    """Add security headers to all responses."""
    @app.after_request
    def add_security_headers(response):
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        
        # XSS protection
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # HSTS (only in production with HTTPS)
        if not app.debug:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        # Content Security Policy (adjust based on your needs)
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:;"
        )
        
        return response


# Authentication decorators
def require_auth(f: Callable) -> Callable:
    """
    Decorator to require authentication for an endpoint.
    Validates JWT token and adds user info to g object.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header:
            logger.warning(f"[{g.get('request_id', 'unknown')}] Missing authentication token")
            return jsonify({
                'success': False,
                'error': 'AUTHENTICATION_REQUIRED',
                'message': 'Authentication token is required'
            }), 401
        
        # Extract token (format: "Bearer <token>")
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            logger.warning(f"[{g.get('request_id', 'unknown')}] Invalid authorization header format")
            return jsonify({
                'success': False,
                'error': 'INVALID_TOKEN_FORMAT',
                'message': 'Invalid authorization header format'
            }), 401
        
        token = parts[1]
        
        try:
            # Verify token (will be implemented in auth.py)
            from src.api.auth import verify_access_token
            user_data = verify_access_token(token)
            
            # Store user data in g for access in route handlers
            g.current_user = user_data
            g.user_id = user_data['user_id']
            g.username = user_data['username']
            g.role = user_data['role']
            
            logger.debug(f"[{g.get('request_id', 'unknown')}] Authenticated user: {g.username}")
            
            return f(*args, **kwargs)
            
        except TokenExpiredError:
            logger.warning(f"[{g.get('request_id', 'unknown')}] Token expired")
            return jsonify({
                'success': False,
                'error': 'TOKEN_EXPIRED',
                'message': 'Authentication token has expired'
            }), 401
            
        except TokenInvalidError:
            logger.warning(f"[{g.get('request_id', 'unknown')}] Invalid token")
            return jsonify({
                'success': False,
                'error': 'TOKEN_INVALID',
                'message': 'Invalid authentication token'
            }), 401
            
        except Exception as e:
            logger.error(f"[{g.get('request_id', 'unknown')}] Token verification failed: {e}")
            return jsonify({
                'success': False,
                'error': 'AUTHENTICATION_ERROR',
                'message': 'Authentication failed'
            }), 401
    
    return decorated_function


def require_role(*allowed_roles: str) -> Callable:
    """
    Decorator to require specific role(s) for an endpoint.
    Must be used after @require_auth decorator.
    
    Args:
        allowed_roles: Role names that are allowed to access the endpoint
    
    Usage:
        @require_auth
        @require_role('ADMIN', 'OPERATOR')
        def admin_endpoint():
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if user is authenticated (should be set by require_auth)
            if not hasattr(g, 'current_user'):
                logger.error(f"[{g.get('request_id', 'unknown')}] require_role used without require_auth")
                return jsonify({
                    'success': False,
                    'error': 'AUTHENTICATION_REQUIRED',
                    'message': 'Authentication is required'
                }), 401
            
            user_role = g.role
            
            # Check if user has required role
            if user_role not in allowed_roles:
                logger.warning(
                    f"[{g.get('request_id', 'unknown')}] "
                    f"User {g.username} (role: {user_role}) attempted to access endpoint requiring {allowed_roles}"
                )
                return jsonify({
                    'success': False,
                    'error': 'INSUFFICIENT_PERMISSIONS',
                    'message': f'This endpoint requires one of these roles: {", ".join(allowed_roles)}'
                }), 403
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator


def optional_auth(f: Callable) -> Callable:
    """
    Decorator for endpoints that can work with or without authentication.
    If token is provided and valid, sets g.current_user.
    If token is missing or invalid, continues without authentication.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == 'bearer':
                token = parts[1]
                
                try:
                    from src.api.auth import verify_access_token
                    user_data = verify_access_token(token)
                    
                    g.current_user = user_data
                    g.user_id = user_data['user_id']
                    g.username = user_data['username']
                    g.role = user_data['role']
                    
                except Exception:
                    # Token invalid or expired, continue without auth
                    pass
        
        return f(*args, **kwargs)
    
    return decorated_function


def audit_log(action: str, resource_type: str = None):
    """
    Decorator to log user actions for audit trail.
    Must be used after @require_auth decorator.
    
    Args:
        action: Action being performed (e.g., 'create', 'update', 'delete')
        resource_type: Type of resource (e.g., 'program', 'user')
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Execute the function
            result = f(*args, **kwargs)
            
            # Log the action if user is authenticated
            if hasattr(g, 'current_user'):
                try:
                    from src.database.db_manager import get_db
                    db = get_db()
                    
                    # Extract resource ID from kwargs or response
                    resource_id = kwargs.get('id') or kwargs.get('program_id') or kwargs.get('user_id')
                    
                    db.log_audit_event(
                        user_id=g.user_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        request_id=g.get('request_id')
                    )
                except Exception as e:
                    logger.error(f"Failed to log audit event: {e}")
            
            return result
        
        return decorated_function
    
    return decorator


def init_middleware(app, config):
    """
    Initialize all middleware for the Flask application.
    
    Args:
        app: Flask application instance
        config: Configuration object
    """
    logger.info("Initializing middleware...")
    
    # Request ID and logging
    request_id_middleware(app)
    
    # CORS (if not using Flask-CORS)
    # cors_middleware(app, config.CORS_ORIGINS)
    
    # Response compression
    response_compression_middleware(app)
    
    # Security headers
    security_headers_middleware(app)
    
    logger.info("Middleware initialization complete")
