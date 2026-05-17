"""
Performance Tracking System

Provides decorators and utilities for tracking operation performance.
"""

import time
import functools
from typing import Callable, Optional, Dict, Any
from contextlib import contextmanager
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger('performance_tracker')


class PerformanceTracker:
    """
    Tracks performance of operations and stores metrics.
    """
    
    def __init__(self, metrics_collector=None):
        """
        Initialize performance tracker.
        
        Args:
            metrics_collector: MetricsCollector instance
        """
        self.metrics_collector = metrics_collector
        logger.info("Performance tracker initialized")
    
    def track(self, operation: str, metadata: Dict = None):
        """
        Decorator to track performance of a function.
        
        Args:
            operation: Name of the operation
            metadata: Optional metadata
        
        Example:
            @performance_tracker.track('api_request')
            def my_endpoint():
                pass
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                success = True
                error = None
                
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    success = False
                    error = str(e)
                    raise
                finally:
                    duration_ms = (time.time() - start_time) * 1000
                    
                    # Record metrics
                    if self.metrics_collector:
                        self.metrics_collector.record(
                            'performance',
                            f'{operation}_duration',
                            duration_ms,
                            tags={
                                'operation': operation,
                                'success': success,
                                'function': func.__name__,
                                **(metadata or {})
                            }
                        )
                    
                    # Log slow operations
                    if duration_ms > 1000:  # > 1 second
                        logger.warning(
                            f"Slow operation: {operation} took {duration_ms:.0f}ms"
                        )
                    else:
                        logger.debug(
                            f"Operation {operation} completed in {duration_ms:.0f}ms"
                        )
            
            return wrapper
        return decorator
    
    @contextmanager
    def measure(self, operation: str, tags: Dict = None):
        """
        Context manager to measure execution time.
        
        Args:
            operation: Name of the operation
            tags: Optional tags
        
        Example:
            with performance_tracker.measure('database_query'):
                cursor.execute(query)
        """
        start_time = time.time()
        
        try:
            yield
        finally:
            duration_ms = (time.time() - start_time) * 1000
            
            if self.metrics_collector:
                self.metrics_collector.record(
                    'performance',
                    f'{operation}_duration',
                    duration_ms,
                    tags={
                        'operation': operation,
                        **(tags or {})
                    }
                )
    
    def record_timing(self, operation: str, duration_ms: float, tags: Dict = None):
        """
        Directly record a timing metric.
        
        Args:
            operation: Name of the operation
            duration_ms: Duration in milliseconds
            tags: Optional tags
        """
        if self.metrics_collector:
            self.metrics_collector.record(
                'performance',
                f'{operation}_duration',
                duration_ms,
                tags={
                    'operation': operation,
                    **(tags or {})
                }
            )


# Global instance
_performance_tracker: Optional[PerformanceTracker] = None


def get_performance_tracker() -> PerformanceTracker:
    """Get global performance tracker instance."""
    global _performance_tracker
    if _performance_tracker is None:
        raise RuntimeError("Performance tracker not initialized")
    return _performance_tracker


def init_performance_tracker(metrics_collector) -> PerformanceTracker:
    """Initialize global performance tracker."""
    global _performance_tracker
    _performance_tracker = PerformanceTracker(metrics_collector)
    return _performance_tracker


def track_performance(operation: str, metadata: Dict = None):
    """
    Convenience decorator for tracking performance.
    
    Example:
        @track_performance('api_request')
        def my_function():
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracker = get_performance_tracker()
            decorated = tracker.track(operation, metadata)(func)
            return decorated(*args, **kwargs)
        return wrapper
    return decorator
