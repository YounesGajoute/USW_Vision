"""
JWT-based authentication and authorization system with RBAC.
Supports user registration, login, token generation, and role-based access control.
"""

import jwt
import bcrypt
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import re

from src.utils.logger import get_logger
from src.utils.exceptions import (
    AuthenticationError, InvalidCredentialsError, TokenExpiredError,
    TokenInvalidError, ValidationError, AccountLockedError
)

logger = get_logger('auth')

# User roles
ROLE_ADMIN = 'ADMIN'
ROLE_OPERATOR = 'OPERATOR'
ROLE_VIEWER = 'VIEWER'

VALID_ROLES = [ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER]

# Password requirements
MIN_PASSWORD_LENGTH = 8
PASSWORD_PATTERN = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*#?&]{8,}$')


class AuthService:
    """Authentication service for managing users and tokens."""
    
    def __init__(self, db_manager, config):
        """
        Initialize authentication service.
        
        Args:
            db_manager: Database manager instance
            config: Application configuration
        """
        self.db = db_manager
        self.secret_key = config.JWT_SECRET_KEY
        self.access_token_expires = config.JWT_ACCESS_TOKEN_EXPIRES
        self.refresh_token_expires = config.JWT_REFRESH_TOKEN_EXPIRES
        self.algorithm = 'HS256'
    
    # ==================== USER MANAGEMENT ====================
    
    def register_user(
        self,
        username: str,
        password: str,
        role: str = ROLE_OPERATOR,
        created_by: int = None
    ) -> Dict:
        """
        Register a new user.
        
        Args:
            username: Username (3-50 characters, alphanumeric + underscore)
            password: Password (min 8 chars, at least one letter and one number)
            role: User role (ADMIN, OPERATOR, or VIEWER)
            created_by: User ID of the creator (for audit trail)
        
        Returns:
            User dictionary
        
        Raises:
            ValidationError: If validation fails
        """
        # Validate username
        if not username or len(username) < 3 or len(username) > 50:
            raise ValidationError("Username must be 3-50 characters", field='username')
        
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            raise ValidationError(
                "Username can only contain letters, numbers, and underscores",
                field='username'
            )
        
        # Check if username already exists
        existing_user = self.db.get_user_by_username(username)
        if existing_user:
            raise ValidationError(f"Username '{username}' already exists", field='username')
        
        # Validate password
        self._validate_password(password)
        
        # Validate role
        if role not in VALID_ROLES:
            raise ValidationError(
                f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}",
                field='role'
            )
        
        # Hash password
        password_hash = self._hash_password(password)
        
        # Create user in database
        user_id = self.db.create_user(
            username=username,
            password_hash=password_hash,
            role=role
        )
        
        logger.info(f"User registered: {username} (role: {role}, id: {user_id})")
        
        # Log audit event
        if created_by:
            self.db.log_audit_event(
                user_id=created_by,
                action='create_user',
                resource_type='user',
                resource_id=user_id,
                details={'username': username, 'role': role}
            )
        
        return {
            'user_id': user_id,
            'username': username,
            'role': role,
            'is_active': True
        }
    
    def authenticate_user(self, username: str, password: str) -> Tuple[Dict, str, str]:
        """
        Authenticate user and generate tokens.
        
        Args:
            username: Username
            password: Password
        
        Returns:
            Tuple of (user_dict, access_token, refresh_token)
        
        Raises:
            InvalidCredentialsError: If credentials are invalid
            AccountLockedError: If account is locked
        """
        # Get user from database
        user = self.db.get_user_by_username(username)
        
        if not user:
            logger.warning(f"Login attempt with non-existent username: {username}")
            # Log failed attempt
            self.db.log_failed_login_attempt(username, 'user_not_found')
            raise InvalidCredentialsError()
        
        # Check if account is active
        if not user['is_active']:
            logger.warning(f"Login attempt for inactive account: {username}")
            self.db.log_failed_login_attempt(username, 'account_inactive')
            raise InvalidCredentialsError("Account is inactive")
        
        # Check if account is locked
        if user.get('is_locked', False):
            logger.warning(f"Login attempt for locked account: {username}")
            raise AccountLockedError()
        
        # Verify password
        if not self._verify_password(password, user['password_hash']):
            logger.warning(f"Failed login attempt for user: {username}")
            
            # Increment failed login attempts
            self.db.increment_failed_login_attempts(user['id'])
            self.db.log_failed_login_attempt(username, 'invalid_password')
            
            # Check if account should be locked
            failed_attempts = self.db.get_failed_login_attempts(user['id'])
            if failed_attempts >= 5:
                self.db.lock_user_account(user['id'])
                logger.warning(f"Account locked due to failed attempts: {username}")
                raise AccountLockedError()
            
            raise InvalidCredentialsError()
        
        # Reset failed login attempts on successful login
        self.db.reset_failed_login_attempts(user['id'])
        
        # Update last login timestamp
        self.db.update_last_login(user['id'])
        
        # Generate tokens
        access_token = self.generate_access_token(user)
        refresh_token = self.generate_refresh_token(user)
        
        # Store refresh token in database
        self.db.store_refresh_token(
            user_id=user['id'],
            token_hash=self._hash_token(refresh_token),
            expires_at=datetime.utcnow() + timedelta(seconds=self.refresh_token_expires)
        )
        
        logger.info(f"User logged in successfully: {username}")
        
        # Log audit event
        self.db.log_audit_event(
            user_id=user['id'],
            action='login',
            resource_type='session',
            details={'username': username}
        )
        
        return (
            {
                'user_id': user['id'],
                'username': user['username'],
                'role': user['role'],
                'is_active': user['is_active']
            },
            access_token,
            refresh_token
        )
    
    def change_password(
        self,
        user_id: int,
        old_password: str,
        new_password: str
    ):
        """
        Change user password.
        
        Args:
            user_id: User ID
            old_password: Current password
            new_password: New password
        
        Raises:
            ValidationError: If validation fails
            InvalidCredentialsError: If old password is incorrect
        """
        # Get user
        user = self.db.get_user_by_id(user_id)
        if not user:
            raise ValidationError("User not found")
        
        # Verify old password
        if not self._verify_password(old_password, user['password_hash']):
            raise InvalidCredentialsError("Current password is incorrect")
        
        # Validate new password
        self._validate_password(new_password)
        
        # Check if new password is different from old
        if old_password == new_password:
            raise ValidationError("New password must be different from current password")
        
        # Hash new password
        new_password_hash = self._hash_password(new_password)
        
        # Update in database
        self.db.update_user_password(user_id, new_password_hash)
        
        # Revoke all existing tokens for security
        self.db.revoke_all_user_tokens(user_id)
        
        logger.info(f"Password changed for user: {user['username']}")
        
        # Log audit event
        self.db.log_audit_event(
            user_id=user_id,
            action='change_password',
            resource_type='user',
            resource_id=user_id
        )
    
    # ==================== TOKEN MANAGEMENT ====================
    
    def generate_access_token(self, user: Dict) -> str:
        """
        Generate JWT access token.
        
        Args:
            user: User dictionary with id, username, role
        
        Returns:
            JWT access token
        """
        payload = {
            'user_id': user['id'],
            'username': user['username'],
            'role': user['role'],
            'token_type': 'access',
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(seconds=self.access_token_expires)
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return token
    
    def generate_refresh_token(self, user: Dict) -> str:
        """
        Generate JWT refresh token.
        
        Args:
            user: User dictionary with id, username
        
        Returns:
            JWT refresh token
        """
        payload = {
            'user_id': user['id'],
            'username': user['username'],
            'token_type': 'refresh',
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(seconds=self.refresh_token_expires)
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return token
    
    def verify_access_token(self, token: str) -> Dict:
        """
        Verify and decode JWT access token.
        
        Args:
            token: JWT token string
        
        Returns:
            Decoded token payload
        
        Raises:
            TokenExpiredError: If token has expired
            TokenInvalidError: If token is invalid
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            
            # Verify token type
            if payload.get('token_type') != 'access':
                raise TokenInvalidError("Invalid token type")
            
            return payload
            
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError()
        except jwt.InvalidTokenError as e:
            raise TokenInvalidError(f"Invalid token: {str(e)}")
    
    def verify_refresh_token(self, token: str) -> Dict:
        """
        Verify and decode JWT refresh token.
        
        Args:
            token: JWT token string
        
        Returns:
            Decoded token payload
        
        Raises:
            TokenExpiredError: If token has expired
            TokenInvalidError: If token is invalid
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            
            # Verify token type
            if payload.get('token_type') != 'refresh':
                raise TokenInvalidError("Invalid token type")
            
            # Check if token is revoked
            token_hash = self._hash_token(token)
            if self.db.is_token_revoked(token_hash):
                raise TokenInvalidError("Token has been revoked")
            
            return payload
            
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError()
        except jwt.InvalidTokenError as e:
            raise TokenInvalidError(f"Invalid token: {str(e)}")
    
    def refresh_access_token(self, refresh_token: str) -> str:
        """
        Generate new access token using refresh token.
        
        Args:
            refresh_token: Valid refresh token
        
        Returns:
            New access token
        
        Raises:
            TokenExpiredError, TokenInvalidError
        """
        # Verify refresh token
        payload = self.verify_refresh_token(refresh_token)
        
        # Get user from database
        user = self.db.get_user_by_id(payload['user_id'])
        if not user or not user['is_active']:
            raise TokenInvalidError("User not found or inactive")
        
        # Generate new access token
        access_token = self.generate_access_token(user)
        
        logger.debug(f"Access token refreshed for user: {user['username']}")
        
        return access_token
    
    def revoke_token(self, token: str):
        """
        Revoke a refresh token.
        
        Args:
            token: Refresh token to revoke
        """
        token_hash = self._hash_token(token)
        self.db.revoke_token(token_hash)
        logger.debug("Token revoked")
    
    def logout(self, refresh_token: str, user_id: int):
        """
        Logout user by revoking their refresh token.
        
        Args:
            refresh_token: User's refresh token
            user_id: User ID for audit logging
        """
        self.revoke_token(refresh_token)
        
        # Log audit event
        self.db.log_audit_event(
            user_id=user_id,
            action='logout',
            resource_type='session'
        )
        
        logger.info(f"User logged out: user_id={user_id}")
    
    # ==================== HELPER METHODS ====================
    
    def _validate_password(self, password: str):
        """
        Validate password meets requirements.
        
        Raises:
            ValidationError: If password doesn't meet requirements
        """
        if not password or len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
                field='password'
            )
        
        if not PASSWORD_PATTERN.match(password):
            raise ValidationError(
                "Password must contain at least one letter and one number",
                field='password'
            )
    
    def _hash_password(self, password: str) -> str:
        """Hash password using bcrypt."""
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password.encode('utf-8'), salt)
        return password_hash.decode('utf-8')
    
    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify password against hash."""
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    
    def _hash_token(self, token: str) -> str:
        """Hash token for storage (using bcrypt for consistency)."""
        import hashlib
        return hashlib.sha256(token.encode('utf-8')).hexdigest()


# Global auth service instance
_auth_service: Optional[AuthService] = None


def init_auth_service(db_manager, config) -> AuthService:
    """
    Initialize global authentication service.
    
    Args:
        db_manager: Database manager instance
        config: Application configuration
    
    Returns:
        AuthService instance
    """
    global _auth_service
    _auth_service = AuthService(db_manager, config)
    logger.info("Authentication service initialized")
    return _auth_service


def get_auth_service() -> AuthService:
    """Get global authentication service instance."""
    if _auth_service is None:
        raise RuntimeError("Auth service not initialized")
    return _auth_service


# Convenience functions for middleware
def verify_access_token(token: str) -> Dict:
    """Verify access token (convenience function for middleware)."""
    return get_auth_service().verify_access_token(token)


def verify_refresh_token(token: str) -> Dict:
    """Verify refresh token (convenience function)."""
    return get_auth_service().verify_refresh_token(token)
