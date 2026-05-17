"""
Metrics Collection System

Collects, stores, and retrieves performance metrics.
"""

import time
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from collections import defaultdict, deque
import json

from src.utils.logger import get_logger

logger = get_logger('metrics_collector')


class MetricsCollector:
    """
    Collects and manages system metrics.
    
    Features:
    - In-memory buffer for recent metrics
    - Periodic database persistence
    - Metric aggregation
    - Thread-safe operations
    """
    
    def __init__(self, db_manager=None, buffer_size=1000, flush_interval=10):
        """
        Initialize metrics collector.
        
        Args:
            db_manager: Database manager instance
            buffer_size: Maximum metrics in memory buffer
            flush_interval: Seconds between database flushes
        """
        self.db_manager = db_manager
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        
        # In-memory buffer for recent metrics
        self._buffer = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
        
        # Aggregated metrics (for quick access)
        self._aggregates = defaultdict(lambda: {
            'count': 0,
            'sum': 0,
            'min': float('inf'),
            'max': float('-inf'),
            'last_value': 0,
            'last_timestamp': None
        })
        
        # Start background flush thread
        self._stop_flag = threading.Event()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()
        
        logger.info("Metrics collector initialized")
    
    def record(self, metric_type: str, metric_name: str, value: float, tags: Dict = None):
        """
        Record a metric.
        
        Args:
            metric_type: Type of metric (system, api, inspection, hardware)
            metric_name: Name of the metric
            value: Metric value
            tags: Optional tags for the metric
        """
        timestamp = datetime.now()
        
        metric = {
            'timestamp': timestamp,
            'metric_type': metric_type,
            'metric_name': metric_name,
            'value': value,
            'tags': tags or {}
        }
        
        with self._lock:
            # Add to buffer
            self._buffer.append(metric)
            
            # Update aggregates
            key = f"{metric_type}.{metric_name}"
            agg = self._aggregates[key]
            agg['count'] += 1
            agg['sum'] += value
            agg['min'] = min(agg['min'], value)
            agg['max'] = max(agg['max'], value)
            agg['last_value'] = value
            agg['last_timestamp'] = timestamp
    
    def get_recent_metrics(self, metric_type: str = None, limit: int = 100) -> List[Dict]:
        """
        Get recent metrics from buffer.
        
        Args:
            metric_type: Optional filter by type
            limit: Maximum number of metrics to return
        
        Returns:
            List of metric dictionaries
        """
        with self._lock:
            metrics = list(self._buffer)
        
        if metric_type:
            metrics = [m for m in metrics if m['metric_type'] == metric_type]
        
        # Return most recent first
        return metrics[-limit:][::-1]
    
    def get_aggregate(self, metric_type: str, metric_name: str) -> Dict:
        """
        Get aggregated statistics for a metric.
        
        Args:
            metric_type: Type of metric
            metric_name: Name of the metric
        
        Returns:
            Dictionary with min, max, avg, count, last_value
        """
        key = f"{metric_type}.{metric_name}"
        
        with self._lock:
            agg = self._aggregates.get(key)
        
        if not agg or agg['count'] == 0:
            return {
                'min': 0,
                'max': 0,
                'avg': 0,
                'count': 0,
                'last_value': 0,
                'last_timestamp': None
            }
        
        return {
            'min': agg['min'] if agg['min'] != float('inf') else 0,
            'max': agg['max'] if agg['max'] != float('-inf') else 0,
            'avg': agg['sum'] / agg['count'],
            'count': agg['count'],
            'last_value': agg['last_value'],
            'last_timestamp': agg['last_timestamp'].isoformat() if agg['last_timestamp'] else None
        }
    
    def get_all_aggregates(self) -> Dict[str, Dict]:
        """Get all aggregate statistics."""
        with self._lock:
            result = {}
            for key, agg in self._aggregates.items():
                if agg['count'] > 0:
                    metric_type, metric_name = key.split('.', 1)
                    if metric_type not in result:
                        result[metric_type] = {}
                    
                    result[metric_type][metric_name] = {
                        'min': agg['min'] if agg['min'] != float('inf') else 0,
                        'max': agg['max'] if agg['max'] != float('-inf') else 0,
                        'avg': agg['sum'] / agg['count'],
                        'count': agg['count'],
                        'last_value': agg['last_value'],
                        'last_timestamp': agg['last_timestamp'].isoformat() if agg['last_timestamp'] else None
                    }
            
            return result
    
    def _flush_to_database(self):
        """Flush buffered metrics to database."""
        if not self.db_manager:
            return
        
        with self._lock:
            if not self._buffer:
                return
            
            # Copy buffer and clear
            metrics_to_save = list(self._buffer)
            self._buffer.clear()
        
        try:
            # Batch insert into database
            with self.db_manager._get_cursor() as cursor:
                for metric in metrics_to_save:
                    cursor.execute("""
                        INSERT INTO metrics (
                            timestamp, metric_type, metric_name, value, tags_json
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (
                        metric['timestamp'],
                        metric['metric_type'],
                        metric['metric_name'],
                        metric['value'],
                        json.dumps(metric['tags'])
                    ))
            
            logger.debug(f"Flushed {len(metrics_to_save)} metrics to database")
            
        except Exception as e:
            logger.error(f"Failed to flush metrics to database: {e}")
            # Put metrics back in buffer (at the front)
            with self._lock:
                for metric in reversed(metrics_to_save):
                    if len(self._buffer) < self.buffer_size:
                        self._buffer.appendleft(metric)
    
    def _flush_loop(self):
        """Background thread that periodically flushes metrics."""
        while not self._stop_flag.is_set():
            try:
                time.sleep(self.flush_interval)
                self._flush_to_database()
            except Exception as e:
                logger.error(f"Error in flush loop: {e}")
    
    def get_historical_metrics(
        self,
        metric_type: str,
        metric_name: str,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 1000
    ) -> List[Dict]:
        """
        Get historical metrics from database.
        
        Args:
            metric_type: Type of metric
            metric_name: Name of the metric
            start_time: Start of time range
            end_time: End of time range
            limit: Maximum number of records
        
        Returns:
            List of metric dictionaries
        """
        if not self.db_manager:
            return []
        
        try:
            with self.db_manager._get_cursor() as cursor:
                query = """
                    SELECT timestamp, value, tags_json
                    FROM metrics
                    WHERE metric_type = ? AND metric_name = ?
                """
                params = [metric_type, metric_name]
                
                if start_time:
                    query += " AND timestamp >= ?"
                    params.append(start_time)
                
                if end_time:
                    query += " AND timestamp <= ?"
                    params.append(end_time)
                
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                
                cursor.execute(query, params)
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'timestamp': row[0],
                        'value': row[1],
                        'tags': json.loads(row[2]) if row[2] else {}
                    })
                
                return results
                
        except Exception as e:
            logger.error(f"Failed to get historical metrics: {e}")
            return []
    
    def cleanup_old_metrics(self, days: int = 7):
        """
        Clean up metrics older than specified days.
        
        Args:
            days: Number of days to keep
        """
        if not self.db_manager:
            return
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        try:
            with self.db_manager._get_cursor() as cursor:
                cursor.execute("""
                    DELETE FROM metrics
                    WHERE timestamp < ?
                """, (cutoff_date,))
                
                deleted = cursor.rowcount
                logger.info(f"Cleaned up {deleted} old metrics (older than {days} days)")
                
        except Exception as e:
            logger.error(f"Failed to cleanup old metrics: {e}")
    
    def reset_aggregates(self):
        """Reset all aggregate statistics."""
        with self._lock:
            self._aggregates.clear()
        logger.info("Reset all aggregate statistics")
    
    def stop(self):
        """Stop the metrics collector and flush remaining metrics."""
        logger.info("Stopping metrics collector...")
        self._stop_flag.set()
        self._flush_thread.join(timeout=5)
        self._flush_to_database()
        logger.info("Metrics collector stopped")


# Global instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get global metrics collector instance."""
    global _metrics_collector
    if _metrics_collector is None:
        raise RuntimeError("Metrics collector not initialized")
    return _metrics_collector


def init_metrics_collector(db_manager, **kwargs) -> MetricsCollector:
    """Initialize global metrics collector."""
    global _metrics_collector
    _metrics_collector = MetricsCollector(db_manager, **kwargs)
    return _metrics_collector
