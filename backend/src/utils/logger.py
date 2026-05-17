"""Logging utility for the Vision Inspection System"""

import logging
import os
from datetime import datetime
from typing import Optional


def setup_logging(config: dict) -> logging.Logger:
    """
    Setup application logging.
    
    Args:
        config: Logging configuration dictionary
        
    Returns:
        Configured logger instance
    """
    log_level = config.get('level', 'INFO')
    log_file = config.get('file', './logs/vision.log')
    
    # Ensure log directory exists
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Create logger
    logger = logging.getLogger('vision_inspection')
    logger.setLevel(getattr(logging, log_level))
    
    # Remove existing handlers
    logger.handlers = []
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(getattr(logging, log_level))
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level))
    console_formatter = logging.Formatter(
        '%(levelname)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get logger instance."""
    if name:
        return logging.getLogger(f'vision_inspection.{name}')
    return logging.getLogger('vision_inspection')

