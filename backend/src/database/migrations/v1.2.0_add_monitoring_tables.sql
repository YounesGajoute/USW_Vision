-- Migration: v1.2.0 - Add monitoring tables
-- Date: 2025-10-08
-- Description: Adds tables for metrics, alerts, and performance tracking

BEGIN TRANSACTION;

-- Metrics table - stores time-series metrics
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    metric_type TEXT NOT NULL,  -- system, api, inspection, hardware, performance
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    tags_json TEXT,
    CONSTRAINT metrics_type_check CHECK (
        metric_type IN ('system', 'api', 'inspection', 'hardware', 'performance')
    )
);

-- Indexes for fast metric queries
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_type_name ON metrics(metric_type, metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_type_timestamp ON metrics(metric_type, timestamp DESC);

-- Alerts table - stores system alerts
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL UNIQUE,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL,  -- info, warning, critical
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    component TEXT,
    acknowledged BOOLEAN DEFAULT 0,
    acknowledged_at DATETIME,
    resolved BOOLEAN DEFAULT 0,
    resolved_at DATETIME,
    metadata_json TEXT,
    CONSTRAINT alerts_level_check CHECK (
        level IN ('info', 'warning', 'critical')
    )
);

-- Indexes for alert queries
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_level ON alerts(level);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);

-- Performance logs table - detailed performance tracking
CREATE TABLE IF NOT EXISTS performance_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    operation TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    success BOOLEAN DEFAULT 1,
    metadata_json TEXT
);

-- Indexes for performance queries
CREATE INDEX IF NOT EXISTS idx_perf_timestamp ON performance_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_perf_operation ON performance_logs(operation);
CREATE INDEX IF NOT EXISTS idx_perf_success ON performance_logs(success);

-- View for metric aggregates (hourly)
CREATE VIEW IF NOT EXISTS metrics_hourly AS
SELECT
    datetime(timestamp, 'start of hour') as hour,
    metric_type,
    metric_name,
    COUNT(*) as count,
    AVG(value) as avg_value,
    MIN(value) as min_value,
    MAX(value) as max_value
FROM metrics
GROUP BY hour, metric_type, metric_name;

-- View for active alerts
CREATE VIEW IF NOT EXISTS active_alerts AS
SELECT *
FROM alerts
WHERE resolved = 0
ORDER BY timestamp DESC;

-- View for recent performance issues (operations taking > 1 second)
CREATE VIEW IF NOT EXISTS slow_operations AS
SELECT
    timestamp,
    operation,
    duration_ms,
    metadata_json
FROM performance_logs
WHERE duration_ms > 1000
ORDER BY timestamp DESC
LIMIT 100;

COMMIT;
