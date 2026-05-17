"""
Alert Management System

Manages alerts, notifications, and alert rules.
"""

import uuid
import threading
from typing import Dict, List, Optional, Callable
from datetime import datetime
from collections import defaultdict
import json

from src.utils.logger import get_logger

logger = get_logger('alerts')


class Alert:
    """Represents a system alert."""
    
    def __init__(
        self,
        level: str,
        title: str,
        message: str,
        component: str = None,
        metadata: Dict = None
    ):
        self.alert_id = f"alert_{uuid.uuid4().hex[:12]}"
        self.timestamp = datetime.now()
        self.level = level  # info, warning, critical
        self.title = title
        self.message = message
        self.component = component
        self.metadata = metadata or {}
        self.acknowledged = False
        self.acknowledged_at = None
        self.resolved = False
        self.resolved_at = None
    
    def to_dict(self) -> Dict:
        """Convert alert to dictionary."""
        return {
            'alert_id': self.alert_id,
            'timestamp': self.timestamp.isoformat(),
            'level': self.level,
            'title': self.title,
            'message': self.message,
            'component': self.component,
            'metadata': self.metadata,
            'acknowledged': self.acknowledged,
            'acknowledged_at': self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            'resolved': self.resolved,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None
        }
    
    def acknowledge(self):
        """Mark alert as acknowledged."""
        self.acknowledged = True
        self.acknowledged_at = datetime.now()
    
    def resolve(self):
        """Mark alert as resolved."""
        self.resolved = True
        self.resolved_at = datetime.now()


class AlertRule:
    """Defines conditions for triggering alerts."""
    
    def __init__(
        self,
        rule_id: str,
        metric_type: str,
        metric_name: str,
        condition: str,  # '>', '<', '>=', '<=', '=='
        threshold: float,
        level: str,
        title: str,
        message_template: str,
        component: str = None
    ):
        self.rule_id = rule_id
        self.metric_type = metric_type
        self.metric_name = metric_name
        self.condition = condition
        self.threshold = threshold
        self.level = level
        self.title = title
        self.message_template = message_template
        self.component = component
        self.last_triggered = None
        self.cooldown = 300  # 5 minutes cooldown
    
    def check(self, value: float) -> bool:
        """
        Check if the rule condition is met.
        
        Args:
            value: Current metric value
        
        Returns:
            True if alert should be triggered
        """
        # Check cooldown
        if self.last_triggered:
            elapsed = (datetime.now() - self.last_triggered).total_seconds()
            if elapsed < self.cooldown:
                return False
        
        # Evaluate condition
        if self.condition == '>':
            return value > self.threshold
        elif self.condition == '<':
            return value < self.threshold
        elif self.condition == '>=':
            return value >= self.threshold
        elif self.condition == '<=':
            return value <= self.threshold
        elif self.condition == '==':
            return value == self.threshold
        
        return False
    
    def create_alert(self, value: float) -> Alert:
        """Create an alert for this rule."""
        self.last_triggered = datetime.now()
        
        message = self.message_template.format(value=value, threshold=self.threshold)
        
        return Alert(
            level=self.level,
            title=self.title,
            message=message,
            component=self.component,
            metadata={
                'rule_id': self.rule_id,
                'metric_type': self.metric_type,
                'metric_name': self.metric_name,
                'value': value,
                'threshold': self.threshold
            }
        )


class AlertManager:
    """
    Manages alerts and alert rules.
    
    Features:
    - Rule-based alerting
    - Alert history
    - Alert notifications
    - Alert acknowledgment
    """
    
    def __init__(self, db_manager=None, metrics_collector=None):
        """
        Initialize alert manager.
        
        Args:
            db_manager: Database manager instance
            metrics_collector: Metrics collector instance
        """
        self.db_manager = db_manager
        self.metrics_collector = metrics_collector
        
        # Active alerts (in memory)
        self._active_alerts: Dict[str, Alert] = {}
        self._lock = threading.Lock()
        
        # Alert rules
        self._rules: List[AlertRule] = []
        
        # Alert listeners (callbacks)
        self._listeners: List[Callable] = []
        
        # Initialize default rules
        self._init_default_rules()
        
        # Start monitoring thread
        self._stop_flag = threading.Event()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        
        logger.info("Alert manager initialized")
    
    def _init_default_rules(self):
        """Initialize default alert rules."""
        # CPU usage alerts
        self.add_rule(AlertRule(
            rule_id='cpu_critical',
            metric_type='system',
            metric_name='cpu_percent',
            condition='>',
            threshold=90,
            level='critical',
            title='Critical CPU Usage',
            message_template='CPU usage is at {value:.1f}% (threshold: {threshold}%)',
            component='system'
        ))
        
        self.add_rule(AlertRule(
            rule_id='cpu_warning',
            metric_type='system',
            metric_name='cpu_percent',
            condition='>',
            threshold=75,
            level='warning',
            title='High CPU Usage',
            message_template='CPU usage is at {value:.1f}% (threshold: {threshold}%)',
            component='system'
        ))
        
        # Memory alerts
        self.add_rule(AlertRule(
            rule_id='memory_critical',
            metric_type='system',
            metric_name='memory_percent',
            condition='>',
            threshold=90,
            level='critical',
            title='Critical Memory Usage',
            message_template='Memory usage is at {value:.1f}% (threshold: {threshold}%)',
            component='system'
        ))
        
        self.add_rule(AlertRule(
            rule_id='memory_warning',
            metric_type='system',
            metric_name='memory_percent',
            condition='>',
            threshold=75,
            level='warning',
            title='High Memory Usage',
            message_template='Memory usage is at {value:.1f}% (threshold: {threshold}%)',
            component='system'
        ))
        
        # Disk space alerts
        self.add_rule(AlertRule(
            rule_id='disk_critical',
            metric_type='system',
            metric_name='disk_percent',
            condition='>',
            threshold=90,
            level='critical',
            title='Critical Disk Space',
            message_template='Disk usage is at {value:.1f}% (threshold: {threshold}%)',
            component='system'
        ))
        
        self.add_rule(AlertRule(
            rule_id='disk_warning',
            metric_type='system',
            metric_name='disk_percent',
            condition='>',
            threshold=80,
            level='warning',
            title='Low Disk Space',
            message_template='Disk usage is at {value:.1f}% (threshold: {threshold}%)',
            component='system'
        ))
    
    def add_rule(self, rule: AlertRule):
        """Add an alert rule."""
        with self._lock:
            self._rules.append(rule)
        logger.info(f"Added alert rule: {rule.rule_id}")
    
    def remove_rule(self, rule_id: str):
        """Remove an alert rule."""
        with self._lock:
            self._rules = [r for r in self._rules if r.rule_id != rule_id]
        logger.info(f"Removed alert rule: {rule_id}")
    
    def create_alert(
        self,
        level: str,
        title: str,
        message: str,
        component: str = None,
        metadata: Dict = None
    ) -> Alert:
        """
        Create a new alert.
        
        Args:
            level: Alert level (info, warning, critical)
            title: Alert title
            message: Alert message
            component: Component name
            metadata: Additional metadata
        
        Returns:
            Created Alert object
        """
        alert = Alert(level, title, message, component, metadata)
        
        with self._lock:
            self._active_alerts[alert.alert_id] = alert
        
        # Notify listeners
        self._notify_listeners(alert)
        
        # Save to database
        self._save_alert_to_db(alert)
        
        logger.info(f"Created alert: [{level}] {title}")
        
        return alert
    
    def get_active_alerts(self, level: str = None) -> List[Alert]:
        """
        Get active (unresolved) alerts.
        
        Args:
            level: Optional filter by level
        
        Returns:
            List of Alert objects
        """
        with self._lock:
            alerts = [a for a in self._active_alerts.values() if not a.resolved]
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)
    
    def get_alert(self, alert_id: str) -> Optional[Alert]:
        """Get alert by ID."""
        with self._lock:
            return self._active_alerts.get(alert_id)
    
    def acknowledge_alert(self, alert_id: str):
        """Acknowledge an alert."""
        alert = self.get_alert(alert_id)
        if alert:
            alert.acknowledge()
            self._update_alert_in_db(alert)
            logger.info(f"Acknowledged alert: {alert_id}")
    
    def resolve_alert(self, alert_id: str):
        """Resolve an alert."""
        alert = self.get_alert(alert_id)
        if alert:
            alert.resolve()
            self._update_alert_in_db(alert)
            logger.info(f"Resolved alert: {alert_id}")
    
    def add_listener(self, callback: Callable):
        """Add an alert listener callback."""
        self._listeners.append(callback)
    
    def _notify_listeners(self, alert: Alert):
        """Notify all listeners of a new alert."""
        for listener in self._listeners:
            try:
                listener(alert)
            except Exception as e:
                logger.error(f"Error notifying listener: {e}")
    
    def _monitor_loop(self):
        """Background thread that checks alert rules."""
        while not self._stop_flag.is_set():
            try:
                if self.metrics_collector:
                    # Get all aggregates
                    aggregates = self.metrics_collector.get_all_aggregates()
                    
                    # Check each rule
                    with self._lock:
                        rules = list(self._rules)
                    
                    for rule in rules:
                        metric_data = aggregates.get(rule.metric_type, {}).get(rule.metric_name)
                        
                        if metric_data:
                            value = metric_data['last_value']
                            
                            if rule.check(value):
                                alert = rule.create_alert(value)
                                
                                with self._lock:
                                    self._active_alerts[alert.alert_id] = alert
                                
                                self._notify_listeners(alert)
                                self._save_alert_to_db(alert)
                
                threading.Event().wait(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"Error in alert monitor loop: {e}")
                threading.Event().wait(10)
    
    def _save_alert_to_db(self, alert: Alert):
        """Save alert to database."""
        if not self.db_manager:
            return
        
        try:
            with self.db_manager._get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO alerts (
                        alert_id, timestamp, level, title, message,
                        component, acknowledged, resolved, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    alert.alert_id,
                    alert.timestamp,
                    alert.level,
                    alert.title,
                    alert.message,
                    alert.component,
                    alert.acknowledged,
                    alert.resolved,
                    json.dumps(alert.metadata)
                ))
        except Exception as e:
            logger.error(f"Failed to save alert to database: {e}")
    
    def _update_alert_in_db(self, alert: Alert):
        """Update alert in database."""
        if not self.db_manager:
            return
        
        try:
            with self.db_manager._get_cursor() as cursor:
                cursor.execute("""
                    UPDATE alerts
                    SET acknowledged = ?,
                        acknowledged_at = ?,
                        resolved = ?,
                        resolved_at = ?
                    WHERE alert_id = ?
                """, (
                    alert.acknowledged,
                    alert.acknowledged_at,
                    alert.resolved,
                    alert.resolved_at,
                    alert.alert_id
                ))
        except Exception as e:
            logger.error(f"Failed to update alert in database: {e}")
    
    def stop(self):
        """Stop the alert manager."""
        logger.info("Stopping alert manager...")
        self._stop_flag.set()
        self._monitor_thread.join(timeout=5)
        logger.info("Alert manager stopped")


# Global instance
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get global alert manager instance."""
    global _alert_manager
    if _alert_manager is None:
        raise RuntimeError("Alert manager not initialized")
    return _alert_manager


def init_alert_manager(db_manager, metrics_collector) -> AlertManager:
    """Initialize global alert manager."""
    global _alert_manager
    _alert_manager = AlertManager(db_manager, metrics_collector)
    return _alert_manager
