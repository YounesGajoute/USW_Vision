"""
Authentication API routes for user management and JWT token handling.
"""

from flask import Blueprint, request, jsonify, g
from functools import wraps

from src.api.schemas import (
    UserRegistrationSchema, UserLoginSchema, PasswordChangeSchema,
    TokenRefreshSchema, UserUpdateSchema, validate_schema
)
from src.api.middleware import require_auth, require_role
from src.api.auth import get_auth_service
from src.utils.logger import get_logger
from src.utils.exceptions import (
    ValidationError, InvalidCredentialsError, AccountLockedError,
    TokenExpiredError, TokenInvalidError, AuthenticationError
)

logger = get_logger('auth_routes')

# Create blueprint
# Mounted at /api/v1 and /api in app_production so paths are /api/v1/auth/* and /api/auth/*
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


# ==================== PUBLIC ENDPOINTS ====================

@auth_bp.route('/login', methods=['POST'])
@validate_schema(UserLoginSchema)
def login():
    """
    POST /api/auth/login
    Body: {username, password}
    Returns: {user, accessToken, refreshToken}
    
    Errors:
        400: Invalid request
        401: Invalid credentials
        423: Account locked
    """
    try:
        data = request.validated_data
        auth_service = get_auth_service()
        
        # Authenticate user
        user, access_token, refresh_token = auth_service.authenticate_user(
            username=data['username'],
            password=data['password']
        )
        
        logger.info(f"User logged in: {user['username']}")
        
        return jsonify({
            'success': True,
            'data': {
                'user': {
                    'id': user['user_id'],
                    'username': user['username'],
                    'role': user['role']
                },
                'accessToken': access_token,
                'refreshToken': refresh_token
            },
            'message': 'Login successful'
        }), 200
        
    except InvalidCredentialsError as e:
        return jsonify({
            'success': False,
            'error': 'INVALID_CREDENTIALS',
            'message': str(e)
        }), 401
        
    except AccountLockedError as e:
        return jsonify({
            'success': False,
            'error': 'ACCOUNT_LOCKED',
            'message': str(e)
        }), 423
        
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return jsonify({
            'success': False,
            'error': 'AUTHENTICATION_ERROR',
            'message': 'Authentication failed'
        }), 500


@auth_bp.route('/refresh', methods=['POST'])
@validate_schema(TokenRefreshSchema)
def refresh_token():
    """
    POST /api/auth/refresh
    Body: {refreshToken}
    Returns: {accessToken}
    
    Errors:
        400: Invalid request
        401: Invalid or expired token
    """
    try:
        data = request.validated_data
        auth_service = get_auth_service()
        
        # Generate new access token
        access_token = auth_service.refresh_access_token(data['refreshToken'])
        
        return jsonify({
            'success': True,
            'data': {
                'accessToken': access_token
            },
            'message': 'Token refreshed successfully'
        }), 200
        
    except (TokenExpiredError, TokenInvalidError) as e:
        return jsonify({
            'success': False,
            'error': e.error_code,
            'message': str(e)
        }), 401
        
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return jsonify({
            'success': False,
            'error': 'REFRESH_ERROR',
            'message': 'Failed to refresh token'
        }), 500


# ==================== PROTECTED ENDPOINTS ====================

@auth_bp.route('/me', methods=['GET'])
@require_auth
def get_current_user():
    """
    GET /api/auth/me
    Returns: Current user information
    
    Requires: Authentication
    """
    try:
        return jsonify({
            'success': True,
            'data': {
                'user': {
                    'id': g.user_id,
                    'username': g.username,
                    'role': g.role
                }
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Get current user failed: {e}")
        return jsonify({
            'success': False,
            'error': 'INTERNAL_ERROR',
            'message': 'Failed to get user information'
        }), 500


@auth_bp.route('/logout', methods=['POST'])
@require_auth
@validate_schema(TokenRefreshSchema)
def logout():
    """
    POST /api/auth/logout
    Body: {refreshToken}
    Returns: Success message
    
    Requires: Authentication
    """
    try:
        data = request.validated_data
        auth_service = get_auth_service()
        
        # Revoke refresh token
        auth_service.logout(data['refreshToken'], g.user_id)
        
        logger.info(f"User logged out: {g.username}")
        
        return jsonify({
            'success': True,
            'message': 'Logout successful'
        }), 200
        
    except Exception as e:
        logger.error(f"Logout failed: {e}")
        return jsonify({
            'success': False,
            'error': 'LOGOUT_ERROR',
            'message': 'Logout failed'
        }), 500


@auth_bp.route('/change-password', methods=['POST'])
@require_auth
@validate_schema(PasswordChangeSchema)
def change_password():
    """
    POST /api/auth/change-password
    Body: {oldPassword, newPassword}
    Returns: Success message
    
    Requires: Authentication
    """
    try:
        data = request.validated_data
        auth_service = get_auth_service()
        
        # Change password
        auth_service.change_password(
            user_id=g.user_id,
            old_password=data['oldPassword'],
            new_password=data['newPassword']
        )
        
        logger.info(f"Password changed for user: {g.username}")
        
        return jsonify({
            'success': True,
            'message': 'Password changed successfully'
        }), 200
        
    except InvalidCredentialsError:
        return jsonify({
            'success': False,
            'error': 'INVALID_PASSWORD',
            'message': 'Current password is incorrect'
        }), 401
        
    except ValidationError as e:
        return jsonify({
            'success': False,
            'error': 'VALIDATION_ERROR',
            'message': str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Password change failed: {e}")
        return jsonify({
            'success': False,
            'error': 'PASSWORD_CHANGE_ERROR',
            'message': 'Failed to change password'
        }), 500


# ==================== USER MANAGEMENT (ADMIN ONLY) ====================

@auth_bp.route('/users', methods=['POST'])
@require_auth
@require_role('ADMIN')
@validate_schema(UserRegistrationSchema)
def create_user():
    """
    POST /api/auth/users
    Body: {username, password, role}
    Returns: Created user
    
    Requires: Admin role
    """
    try:
        data = request.validated_data
        auth_service = get_auth_service()
        
        # Register user
        user = auth_service.register_user(
            username=data['username'],
            password=data['password'],
            role=data.get('role', 'OPERATOR'),
            created_by=g.user_id
        )
        
        logger.info(f"User created by {g.username}: {user['username']}")
        
        return jsonify({
            'success': True,
            'data': {
                'user': user
            },
            'message': 'User created successfully'
        }), 201
        
    except ValidationError as e:
        return jsonify({
            'success': False,
            'error': 'VALIDATION_ERROR',
            'message': str(e),
            'field': e.details.get('field')
        }), 400
        
    except Exception as e:
        logger.error(f"User creation failed: {e}")
        return jsonify({
            'success': False,
            'error': 'USER_CREATION_ERROR',
            'message': 'Failed to create user'
        }), 500


@auth_bp.route('/users', methods=['GET'])
@require_auth
@require_role('ADMIN')
def list_users():
    """
    GET /api/auth/users
    Returns: List of all users
    
    Requires: Admin role
    """
    try:
        from src.database.db_manager import get_db
        db = get_db()
        
        users = db.list_users()
        
        # Remove sensitive data
        for user in users:
            user.pop('password_hash', None)
        
        return jsonify({
            'success': True,
            'data': {
                'users': users
            }
        }), 200
        
    except Exception as e:
        logger.error(f"List users failed: {e}")
        return jsonify({
            'success': False,
            'error': 'INTERNAL_ERROR',
            'message': 'Failed to list users'
        }), 500


@auth_bp.route('/users/<int:user_id>', methods=['GET'])
@require_auth
@require_role('ADMIN')
def get_user(user_id):
    """
    GET /api/auth/users/:id
    Returns: User details
    
    Requires: Admin role
    """
    try:
        from src.database.db_manager import get_db
        db = get_db()
        
        user = db.get_user_by_id(user_id)
        
        if not user:
            return jsonify({
                'success': False,
                'error': 'USER_NOT_FOUND',
                'message': 'User not found'
            }), 404
        
        # Remove sensitive data
        user.pop('password_hash', None)
        
        return jsonify({
            'success': True,
            'data': {
                'user': user
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Get user failed: {e}")
        return jsonify({
            'success': False,
            'error': 'INTERNAL_ERROR',
            'message': 'Failed to get user'
        }), 500


@auth_bp.route('/users/<int:user_id>', methods=['PUT'])
@require_auth
@require_role('ADMIN')
@validate_schema(UserUpdateSchema)
def update_user(user_id):
    """
    PUT /api/auth/users/:id
    Body: {role?, is_active?}
    Returns: Success message
    
    Requires: Admin role
    """
    try:
        data = request.validated_data
        from src.database.db_manager import get_db
        db = get_db()
        
        # Don't allow admin to deactivate themselves
        if user_id == g.user_id and data.get('is_active') == False:
            return jsonify({
                'success': False,
                'error': 'INVALID_OPERATION',
                'message': 'Cannot deactivate your own account'
            }), 400
        
        success = db.update_user(user_id, data)
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'USER_NOT_FOUND',
                'message': 'User not found'
            }), 404
        
        logger.info(f"User {user_id} updated by {g.username}")
        
        return jsonify({
            'success': True,
            'message': 'User updated successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Update user failed: {e}")
        return jsonify({
            'success': False,
            'error': 'UPDATE_ERROR',
            'message': 'Failed to update user'
        }), 500


@auth_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_auth
@require_role('ADMIN')
def delete_user(user_id):
    """
    DELETE /api/auth/users/:id
    Returns: Success message
    
    Requires: Admin role
    """
    try:
        from src.database.db_manager import get_db
        db = get_db()
        
        # Don't allow admin to delete themselves
        if user_id == g.user_id:
            return jsonify({
                'success': False,
                'error': 'INVALID_OPERATION',
                'message': 'Cannot delete your own account'
            }), 400
        
        success = db.delete_user(user_id)
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'USER_NOT_FOUND',
                'message': 'User not found'
            }), 404
        
        logger.info(f"User {user_id} deleted by {g.username}")
        
        return jsonify({
            'success': True,
            'message': 'User deleted successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Delete user failed: {e}")
        return jsonify({
            'success': False,
            'error': 'DELETE_ERROR',
            'message': 'Failed to delete user'
        }), 500
