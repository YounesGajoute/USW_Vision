"""
System Resource Monitoring

Monitors system resources like CPU, memory, disk, etc.
"""

import psutil
import threading
import time
from typing import Dict, Optional
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger('system_monitor')


class SystemMonitor:
    """
    Monitors system resources and collects metrics.
    
    Tracks:
    - CPU usage
    - Memory usage
    - Disk usage
    - Network I/O
    - Process information
    """
    
    def __init__(self, metrics_collector=None, interval=5):
        """
        Initialize system monitor.
        
        Args:
            metrics_collector: MetricsCollector instance
            interval: Seconds between measurements
        """
        self.metrics_collector = metrics_collector
        self.interval = interval
        
        # Start monitoring thread
        self._stop_flag = threading.Event()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        
        logger.info(f"System monitor initialized (interval: {interval}s)")
    
    def get_system_stats(self) -> Dict:
        """
        Get current system statistics.
        
        Returns:
            Dictionary with CPU, memory, disk stats
        """
        try:
            # CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count()
            
            # Memory
            memory = psutil.virtual_memory()
            
            # Disk
            disk = psutil.disk_usage('/')
            
            # Network (if available)
            try:
                network = psutil.net_io_counters()
                network_stats = {
                    'bytes_sent': network.bytes_sent,
                    'bytes_recv': network.bytes_recv,
                    'packets_sent': network.packets_sent,
                    'packets_recv': network.packets_recv
                }
            except:
                network_stats = {}
            
            # Process info
            process = psutil.Process()
            process_memory = process.memory_info()
            
            return {
                'timestamp': datetime.now().isoformat(),
                'cpu': {
                    'percent': cpu_percent,
                    'count': cpu_count
                },
                'memory': {
                    'total': memory.total,
                    'available': memory.available,
                    'used': memory.used,
                    'percent': memory.percent
                },
                'disk': {
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free,
                    'percent': disk.percent
                },
                'network': network_stats,
                'process': {
                    'memory_rss': process_memory.rss,
                    'memory_vms': process_memory.vms,
                    'num_threads': process.num_threads(),
                    'cpu_percent': process.cpu_percent()
                }
            }
        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")
            return {}
    
    def _monitor_loop(self):
        """Background thread that periodically collects system metrics."""
        while not self._stop_flag.is_set():
            try:
                stats = self.get_system_stats()
                
                if self.metrics_collector and stats:
                    # Record CPU metrics
                    if 'cpu' in stats:
                        self.metrics_collector.record(
                            'system',
                            'cpu_percent',
                            stats['cpu']['percent']
                        )
                    
                    # Record memory metrics
                    if 'memory' in stats:
                        self.metrics_collector.record(
                            'system',
                            'memory_percent',
                            stats['memory']['percent']
                        )
                        self.metrics_collector.record(
                            'system',
                            'memory_used_mb',
                            stats['memory']['used'] / (1024 * 1024)
                        )
                    
                    # Record disk metrics
                    if 'disk' in stats:
                        self.metrics_collector.record(
                            'system',
                            'disk_percent',
                            stats['disk']['percent']
                        )
                        self.metrics_collector.record(
                            'system',
                            'disk_free_gb',
                            stats['disk']['free'] / (1024 * 1024 * 1024)
                        )
                    
                    # Record process metrics
                    if 'process' in stats:
                        self.metrics_collector.record(
                            'system',
                            'process_memory_mb',
                            stats['process']['memory_rss'] / (1024 * 1024)
                        )
                        self.metrics_collector.record(
                            'system',
                            'process_threads',
                            stats['process']['num_threads']
                        )
                
                time.sleep(self.interval)
                
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                time.sleep(self.interval)
    
    def get_health_status(self) -> Dict:
        """
        Get overall health status based on system metrics.
        
        Returns:
            Dictionary with health status and warnings
        """
        stats = self.get_system_stats()
        
        health = {
            'status': 'healthy',
            'components': {},
            'warnings': [],
            'critical': []
        }
        
        if not stats:
            health['status'] = 'unknown'
            return health
        
        # Check CPU
        cpu_percent = stats.get('cpu', {}).get('percent', 0)
        if cpu_percent > 90:
            health['components']['cpu'] = 'critical'
            health['critical'].append(f'CPU usage very high: {cpu_percent:.1f}%')
            health['status'] = 'critical'
        elif cpu_percent > 75:
            health['components']['cpu'] = 'warning'
            health['warnings'].append(f'CPU usage high: {cpu_percent:.1f}%')
            if health['status'] == 'healthy':
                health['status'] = 'degraded'
        else:
            health['components']['cpu'] = 'healthy'
        
        # Check Memory
        memory_percent = stats.get('memory', {}).get('percent', 0)
        if memory_percent > 90:
            health['components']['memory'] = 'critical'
            health['critical'].append(f'Memory usage critical: {memory_percent:.1f}%')
            health['status'] = 'critical'
        elif memory_percent > 75:
            health['components']['memory'] = 'warning'
            health['warnings'].append(f'Memory usage high: {memory_percent:.1f}%')
            if health['status'] == 'healthy':
                health['status'] = 'degraded'
        else:
            health['components']['memory'] = 'healthy'
        
        # Check Disk
        disk_percent = stats.get('disk', {}).get('percent', 0)
        if disk_percent > 90:
            health['components']['disk'] = 'critical'
            health['critical'].append(f'Disk space critical: {disk_percent:.1f}% used')
            health['status'] = 'critical'
        elif disk_percent > 80:
            health['components']['disk'] = 'warning'
            health['warnings'].append(f'Disk space low: {disk_percent:.1f}% used')
            if health['status'] == 'healthy':
                health['status'] = 'degraded'
        else:
            health['components']['disk'] = 'healthy'
        
        return health
    
    def stop(self):
        """Stop the system monitor."""
        logger.info("Stopping system monitor...")
        self._stop_flag.set()
        self._monitor_thread.join(timeout=5)
        logger.info("System monitor stopped")


# Global instance
_system_monitor: Optional[SystemMonitor] = None


def get_system_monitor() -> SystemMonitor:
    """Get global system monitor instance."""
    global _system_monitor
    if _system_monitor is None:
        raise RuntimeError("System monitor not initialized")
    return _system_monitor


def init_system_monitor(metrics_collector, **kwargs) -> SystemMonitor:
    """Initialize global system monitor."""
    global _system_monitor
    _system_monitor = SystemMonitor(metrics_collector, **kwargs)
    return _system_monitor
