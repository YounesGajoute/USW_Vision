"""Input validation utilities"""

from typing import Any, Dict, List
from functools import wraps
from flask import request, jsonify


def validate_json_request(required_fields: List[str] = None):
    """
    Decorator to validate JSON request data.
    
    Args:
        required_fields: List of required field names
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Check content type
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 400
            
            # Get JSON data
            data = request.get_json()
            
            if data is None:
                return jsonify({'error': 'Invalid JSON'}), 400
            
            # Check required fields
            if required_fields:
                missing_fields = [field for field in required_fields if field not in data]
                if missing_fields:
                    return jsonify({
                        'error': f'Missing required fields: {", ".join(missing_fields)}'
                    }), 400
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


def validate_file_upload(allowed_extensions: List[str] = None, max_size_mb: int = 10):
    """
    Decorator to validate file uploads.
    
    Args:
        allowed_extensions: List of allowed file extensions (e.g., ['jpg', 'png'])
        max_size_mb: Maximum file size in megabytes
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Check if file is present
            if 'file' not in request.files:
                return jsonify({'error': 'No file provided'}), 400
            
            file = request.files['file']
            
            # Check if filename is present
            if file.filename == '':
                return jsonify({'error': 'No file selected'}), 400
            
            # Check file extension
            if allowed_extensions:
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                if ext not in allowed_extensions:
                    return jsonify({
                        'error': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'
                    }), 400
            
            # Check file size (read first chunk to estimate)
            file.seek(0, 2)  # Seek to end
            size_bytes = file.tell()
            file.seek(0)  # Reset to beginning
            
            if size_bytes > max_size_mb * 1024 * 1024:
                return jsonify({
                    'error': f'File too large. Maximum size: {max_size_mb}MB'
                }), 400
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent directory traversal attacks.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    import re
    import os
    
    # Remove path components
    filename = os.path.basename(filename)
    
    # Remove any non-alphanumeric characters except dots, dashes, underscores
    filename = re.sub(r'[^\w\.\-]', '_', filename)
    
    # Limit length
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:255-len(ext)] + ext
    
    return filename


def validate_range(value: Any, min_val: float, max_val: float, param_name: str):
    """
    Validate that a value is within a specified range.
    
    Args:
        value: Value to validate
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        param_name: Parameter name for error message
        
    Raises:
        ValueError: If value is out of range
    """
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{param_name} must be a number")
    
    if not (min_val <= value <= max_val):
        raise ValueError(f"{param_name} must be between {min_val} and {max_val}")
    
    return value

