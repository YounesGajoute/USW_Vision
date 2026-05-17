"""Monitoring and Diagnostics API Routes"""

from flask import Blueprint, request, jsonify
import traceback
from datetime import datetime, timedelta
import os
import platform
import sys

from src.monitoring import (
    get_metrics_collector,
    get_performance_tracker,
    get_system_monitor,
    get_alert_manager
)
from src.utils.logger import get_logger

logger = get_logger('monitoring_api')

# Create monitoring blueprint
monitoring_api = Blueprint('monitoring', __name__)


# ==================== HEALTH CHECK ====================

@monitoring_api.route('/health', methods=['GET'])
def health_check():
    """
    GET /api/monitoring/health
    Returns: System health status
    
    Provides comprehensive health check including all components.
    """
    try:
        system_monitor = get_system_monitor()
        alert_manager = get_alert_manager()
        
        # Get system health
        health = system_monitor.get_health_status()
        
        # Add active alerts count
        active_alerts = alert_manager.get_active_alerts()
        health['active_alerts'] = {
            'total': len(active_alerts),
            'critical': len([a for a in active_alerts if a.level == 'critical']),
            'warning': len([a for a in active_alerts if a.level == 'warning']),
            'info': len([a for a in active_alerts if a.level == 'info'])
        }
        
        # Overall status code
        status_code = 200
        if health['status'] == 'critical':
            status_code = 503
        elif health['status'] == 'degraded':
            status_code = 200  # Still operational
        
        return jsonify(health), status_code
        
    except Exception as e:
        logger.error(f"Health check failed: {e}\n{traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


# ==================== METRICS ENDPOINTS ====================

@monitoring_api.route('/metrics', methods=['GET'])
def get_current_metrics():
    """
    GET /api/monitoring/metrics
    Query params: ?type=system&limit=100
    Returns: Current metrics and aggregates
    """
    try:
        metrics_collector = get_metrics_collector()
        
        metric_type = request.args.get('type')
        limit = int(request.args.get('limit', 100))
        
        # Get recent metrics
        recent = metrics_collector.get_recent_metrics(metric_type, limit)
        
        # Get aggregates
        aggregates = metrics_collector.get_all_aggregates()
        
        # Format for response
        response = {
            'timestamp': datetime.now().isoformat(),
            'recent_metrics': [
                {
                    'timestamp': m['timestamp'].isoformat(),
                    'type': m['metric_type'],
                    'name': m['metric_name'],
                    'value': m['value'],
                    'tags': m['tags']
                }
                for m in recent
            ],
            'aggregates': aggregates
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f"Get metrics failed: {e}")
        return jsonify({'error': str(e)}), 500


@monitoring_api.route('/metrics/history', methods=['GET'])
def get_metrics_history():
    """
    GET /api/monitoring/metrics/history
    Query params: ?type=system&name=cpu_percent&start=...&end=...&limit=1000
    Returns: Historical metrics
    """
    try:
        metrics_collector = get_metrics_collector()
        
        metric_type = request.args.get('type', 'system')
        metric_name = request.args.get('name', 'cpu_percent')
        limit = int(request.args.get('limit', 1000))
        
        # Parse time range
        start_time = None
        end_time = None
        
        if request.args.get('start'):
            start_time = datetime.fromisoformat(request.args.get('start'))
        
        if request.args.get('end'):
            end_time = datetime.fromisoformat(request.args.get('end'))
        
        # Get historical data
        history = metrics_collector.get_historical_metrics(
            metric_type,
            metric_name,
            start_time,
            end_time,
            limit
        )
        
        return jsonify({
            'metric_type': metric_type,
            'metric_name': metric_name,
            'count': len(history),
            'data': history
        }), 200
        
    except Exception as e:
        logger.error(f"Get metrics history failed: {e}")
        return jsonify({'error': str(e)}), 500


@monitoring_api.route('/metrics/system', methods=['GET'])
def get_system_metrics():
    """
    GET /api/monitoring/metrics/system
    Returns: Current system resource metrics
    """
    try:
        system_monitor = get_system_monitor()
        stats = system_monitor.get_system_stats()
        
        return jsonify(stats), 200
        
    except Exception as e:
        logger.error(f"Get system metrics failed: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== ALERTS ENDPOINTS ====================

@monitoring_api.route('/alerts', methods=['GET'])
def get_alerts():
    """
    GET /api/monitoring/alerts
    Query params: ?level=critical&resolved=false
    Returns: List of alerts
    """
    try:
        alert_manager = get_alert_manager()
        
        level = request.args.get('level')
        resolved = request.args.get('resolved', 'false').lower() == 'true'
        
        if resolved:
            # Would need to implement get_all_alerts including resolved
            alerts = alert_manager.get_active_alerts(level)
        else:
            alerts = alert_manager.get_active_alerts(level)
        
        return jsonify({
            'count': len(alerts),
            'alerts': [alert.to_dict() for alert in alerts]
        }), 200
        
    except Exception as e:
        logger.error(f"Get alerts failed: {e}")
        return jsonify({'error': str(e)}), 500


@monitoring_api.route('/alerts/<alert_id>', methods=['GET'])
def get_alert(alert_id):
    """
    GET /api/monitoring/alerts/:id
    Returns: Alert details
    """
    try:
        alert_manager = get_alert_manager()
        alert = alert_manager.get_alert(alert_id)
        
        if not alert:
            return jsonify({'error': 'Alert not found'}), 404
        
        return jsonify(alert.to_dict()), 200
        
    except Exception as e:
        logger.error(f"Get alert failed: {e}")
        return jsonify({'error': str(e)}), 500


@monitoring_api.route('/alerts/<alert_id>/acknowledge', methods=['POST'])
def acknowledge_alert(alert_id):
    """
    POST /api/monitoring/alerts/:id/acknowledge
    Returns: Success message
    """
    try:
        alert_manager = get_alert_manager()
        alert_manager.acknowledge_alert(alert_id)
        
        return jsonify({
            'message': 'Alert acknowledged',
            'alert_id': alert_id
        }), 200
        
    except Exception as e:
        logger.error(f"Acknowledge alert failed: {e}")
        return jsonify({'error': str(e)}), 500


@monitoring_api.route('/alerts/<alert_id>/resolve', methods=['POST'])
def resolve_alert(alert_id):
    """
    POST /api/monitoring/alerts/:id/resolve
    Returns: Success message
    """
    try:
        alert_manager = get_alert_manager()
        alert_manager.resolve_alert(alert_id)
        
        return jsonify({
            'message': 'Alert resolved',
            'alert_id': alert_id
        }), 200
        
    except Exception as e:
        logger.error(f"Resolve alert failed: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== DIAGNOSTICS ENDPOINTS ====================

@monitoring_api.route('/diagnostics', methods=['GET'])
def run_diagnostics():
    """
    GET /api/monitoring/diagnostics
    Returns: Comprehensive diagnostic information
    """
    try:
        system_monitor = get_system_monitor()
        metrics_collector = get_metrics_collector()
        
        # System information
        system_info = {
            'platform': platform.system(),
            'platform_release': platform.release(),
            'platform_version': platform.version(),
            'architecture': platform.machine(),
            'hostname': platform.node(),
            'processor': platform.processor(),
            'python_version': sys.version,
            'python_executable': sys.executable
        }
        
        # System resources
        system_stats = system_monitor.get_system_stats()
        
        # Health status
        health = system_monitor.get_health_status()
        
        # Metrics summary
        aggregates = metrics_collector.get_all_aggregates()
        
        # Database info
        db_info = {}
        try:
            from src.database.db_manager import get_db
            db = get_db()
            
            # Get database size
            db_path = db.db_path
            if os.path.exists(db_path):
                db_size = os.path.getsize(db_path)
                db_info['size_bytes'] = db_size
                db_info['size_mb'] = db_size / (1024 * 1024)
                db_info['path'] = db_path
        except:
            pass
        
        diagnostics = {
            'timestamp': datetime.now().isoformat(),
            'system_info': system_info,
            'system_stats': system_stats,
            'health': health,
            'metrics_summary': {
                'types': list(aggregates.keys()),
                'total_metrics': sum(
                    len(metrics) for metrics in aggregates.values()
                )
            },
            'database': db_info
        }
        
        return jsonify(diagnostics), 200
        
    except Exception as e:
        logger.error(f"Diagnostics failed: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500


@monitoring_api.route('/diagnostics/performance', methods=['GET'])
def get_performance_report():
    """
    GET /api/monitoring/diagnostics/performance
    Returns: Performance analysis and slowest operations
    """
    try:
        metrics_collector = get_metrics_collector()
        
        # Get performance metrics
        perf_aggregates = metrics_collector.get_all_aggregates().get('performance', {})
        
        # Sort by average duration
        operations = []
        for metric_name, stats in perf_aggregates.items():
            if '_duration' in metric_name:
                operation = metric_name.replace('_duration', '')
                operations.append({
                    'operation': operation,
                    'avg_duration_ms': stats['avg'],
                    'min_duration_ms': stats['min'],
                    'max_duration_ms': stats['max'],
                    'count': stats['count'],
                    'last_duration_ms': stats['last_value']
                })
        
        # Sort by average duration (slowest first)
        operations.sort(key=lambda x: x['avg_duration_ms'], reverse=True)
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'total_operations': len(operations),
            'operations': operations[:20],  # Top 20 slowest
            'summary': {
                'slowest_operation': operations[0] if operations else None,
                'fastest_operation': operations[-1] if operations else None
            }
        }
        
        return jsonify(report), 200
        
    except Exception as e:
        logger.error(f"Performance report failed: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== UTILITY ENDPOINTS ====================

@monitoring_api.route('/ping', methods=['GET'])
def ping():
    """
    GET /api/monitoring/ping
    Returns: Simple ping response
    """
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat()
    }), 200


@monitoring_api.route('/info', methods=['GET'])
def get_info():
    """
    GET /api/monitoring/info
    Returns: System information
    """
    try:
        info = {
            'timestamp': datetime.now().isoformat(),
            'platform': platform.system(),
            'platform_version': platform.version(),
            'python_version': platform.python_version(),
            'hostname': platform.node()
        }
        
        return jsonify(info), 200
        
    except Exception as e:
        logger.error(f"Get info failed: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== ERROR HANDLERS ====================

@monitoring_api.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404


@monitoring_api.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500
