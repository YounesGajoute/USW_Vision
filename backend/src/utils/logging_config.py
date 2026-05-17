"""
Enhanced logging configuration with rotation, JSON formatting, and correlation IDs.
Supports multiple log outputs and production-ready logging.
"""

import logging
import logging.handlers
import json
import os
from datetime import datetime
from typing import Dict, Any
from flask import has_request_context, g


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            'timestamp': datetime.utcfromtimestamp(record.created).isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # Add request ID if available (from Flask g object)
        if has_request_context() and hasattr(g, 'request_id'):
            log_data['request_id'] = g.request_id
        
        # Add user ID if available
        if has_request_context() and hasattr(g, 'user_id'):
            log_data['user_id'] = g.user_id
        
        # Add extra fields from record
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id
        
        if hasattr(record, 'user_id'):
            log_data['user_id'] = record.user_id
        
        if hasattr(record, 'duration_ms'):
            log_data['duration_ms'] = record.duration_ms
        
        if hasattr(record, 'status_code'):
            log_data['status_code'] = record.status_code
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': self.formatException(record.exc_info)
            }
        
        # Add source location
        log_data['source'] = {
            'file': record.pathname,
            'line': record.lineno,
            'function': record.funcName
        }
        
        return json.dumps(log_data)


class StandardFormatter(logging.Formatter):
    """Standard human-readable log formatter with colors (for console)."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'
    }
    
    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with optional colors."""
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        level = record.levelname
        
        # Add color if enabled
        if self.use_colors and level in self.COLORS:
            level = f"{self.COLORS[level]}{level}{self.COLORS['RESET']}"
        
        # Build log message
        message = f"{timestamp} | {level:8s} | {record.name:20s} | {record.getMessage()}"
        
        # Add request ID if available
        if has_request_context() and hasattr(g, 'request_id'):
            message = f"[{g.request_id[:8]}] {message}"
        
        # Add exception info if present
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)
        
        return message


def setup_logging(config: Dict[str, Any]) -> logging.Logger:
    """
    Setup comprehensive logging configuration.
    
    Args:
        config: Configuration dictionary with logging settings
    
    Returns:
        Root logger instance
    """
    log_level = config.get('LOG_LEVEL', 'INFO').upper()
    log_file = config.get('LOG_FILE', './logs/app.log')
    use_json = config.get('LOG_JSON_FORMAT', False)
    max_bytes = config.get('LOG_MAX_BYTES', 10485760)  # 10MB
    backup_count = config.get('LOG_BACKUP_COUNT', 30)
    
    # Ensure log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    
    # Create root logger
    root_logger = logging.getLogger('vision_inspection')
    root_logger.setLevel(getattr(logging, log_level))
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # ==================== FILE HANDLER (Rotating) ====================
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(getattr(logging, log_level))
    
    # Use JSON formatter for file if configured
    if use_json:
        file_handler.setFormatter(JSONFormatter())
    else:
        file_handler.setFormatter(StandardFormatter(use_colors=False))
    
    root_logger.addHandler(file_handler)
    
    # ==================== ERROR FILE HANDLER ====================
    error_log_file = log_file.replace('.log', '_error.log')
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    
    if use_json:
        error_handler.setFormatter(JSONFormatter())
    else:
        error_handler.setFormatter(StandardFormatter(use_colors=False))
    
    root_logger.addHandler(error_handler)
    
    # ==================== CONSOLE HANDLER ====================
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level))
    console_handler.setFormatter(StandardFormatter(use_colors=True))
    
    root_logger.addHandler(console_handler)
    
    # ==================== ACCESS LOG HANDLER ====================
    access_log_file = os.path.join(log_dir, 'access.log') if log_dir else './logs/access.log'
    access_handler = logging.handlers.RotatingFileHandler(
        access_log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    access_handler.setLevel(logging.INFO)
    
    if use_json:
        access_handler.setFormatter(JSONFormatter())
    else:
        access_handler.setFormatter(StandardFormatter(use_colors=False))
    
    # Create separate logger for access logs
    access_logger = logging.getLogger('vision_inspection.access')
    access_logger.addHandler(access_handler)
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False  # Don't propagate to root logger
    
    # Silence noisy third-party loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    
    root_logger.info(f"Logging initialized - Level: {log_level}, Format: {'JSON' if use_json else 'Standard'}")
    
    return root_logger


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance with optional module name.
    
    Args:
        name: Module name to append to base logger name
    
    Returns:
        Logger instance
    """
    if name:
        return logging.getLogger(f'vision_inspection.{name}')
    return logging.getLogger('vision_inspection')


class RequestLogger:
    """
    Context manager for logging request/response with timing.
    
    Usage:
        with RequestLogger('create_program', program_id=123) as log:
            # ... operation ...
            log.add_context('result', 'success')
    """
    
    def __init__(self, operation: str, **context):
        self.logger = get_logger('request')
        self.operation = operation
        self.context = context
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.utcnow()
        self.logger.info(f"Starting: {self.operation}", extra=self.context)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.utcnow() - self.start_time).total_seconds() * 1000
        
        if exc_type:
            self.logger.error(
                f"Failed: {self.operation} - {exc_type.__name__}: {exc_val}",
                extra={**self.context, 'duration_ms': duration},
                exc_info=True
            )
        else:
            self.logger.info(
                f"Completed: {self.operation}",
                extra={**self.context, 'duration_ms': duration}
            )
        
        return False  # Don't suppress exceptions
    
    def add_context(self, key: str, value: Any):
        """Add additional context to the log."""
        self.context[key] = value


class AuditLogger:
    """
    Specialized logger for audit trail.
    Logs all user actions for compliance and security.
    """
    
    def __init__(self):
        self.logger = get_logger('audit')
    
    def log_action(
        self,
        user_id: int,
        username: str,
        action: str,
        resource_type: str = None,
        resource_id: int = None,
        details: Dict[str, Any] = None,
        ip_address: str = None
    ):
        """
        Log user action for audit trail.
        
        Args:
            user_id: User ID performing the action
            username: Username
            action: Action performed (create, update, delete, etc.)
            resource_type: Type of resource (program, user, etc.)
            resource_id: ID of the resource
            details: Additional details about the action
            ip_address: IP address of the user
        """
        audit_data = {
            'user_id': user_id,
            'username': username,
            'action': action,
            'resource_type': resource_type,
            'resource_id': resource_id,
            'details': details or {},
            'ip_address': ip_address,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }
        
        if has_request_context() and hasattr(g, 'request_id'):
            audit_data['request_id'] = g.request_id
        
        self.logger.info(
            f"AUDIT: {username} performed {action} on {resource_type}#{resource_id}",
            extra=audit_data
        )


# Global audit logger instance
_audit_logger = None


def get_audit_logger() -> AuditLogger:
    """Get global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
