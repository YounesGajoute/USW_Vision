"""
Gunicorn Configuration for Production Deployment
=================================================

This configuration file optimizes Gunicorn for production use with
Flask-SocketIO and WebSocket support.

Usage:
    gunicorn -c gunicorn_config.py wsgi:app
"""

import os
import multiprocessing

# ==================== Server Socket ====================

bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5000')
backlog = 2048  # Maximum number of pending connections

# ==================== Worker Processes ====================

# Worker class - MUST be eventlet or gevent for SocketIO support
worker_class = 'eventlet'  # Alternative: 'gevent'

# Number of worker processes
# For SocketIO, usually 1 worker is sufficient due to async nature
# Can be increased if needed, but SocketIO state must be shared
workers = int(os.environ.get('GUNICORN_WORKERS', 1))

# Alternative formula for CPU-bound workloads (not recommended for SocketIO):
# workers = multiprocessing.cpu_count() * 2 + 1

# Worker connections (only relevant for eventlet/gevent)
worker_connections = 1000  # Max simultaneous connections per worker

# ==================== Worker Lifecycle ====================

# Timeout for worker processes (seconds)
timeout = 120  # Increased for long-running inspections
graceful_timeout = 30  # Time to wait for workers to finish during reload
keepalive = 5  # Seconds to wait for requests on Keep-Alive connections

# Worker restart settings
max_requests = 1000  # Restart worker after this many requests (prevents memory leaks)
max_requests_jitter = 50  # Randomize restart to avoid all workers restarting at once

# ==================== Server Mechanics ====================

# Daemon mode - set to True for background operation
daemon = False  # Should be False when using systemd

# Process naming
proc_name = 'vision_inspection_backend'

# PID file
pidfile = '/tmp/gunicorn_vision_inspection.pid'

# User/Group (if running as root, drop privileges)
# user = 'www-data'
# group = 'www-data'

# ==================== Logging ====================

# Access log
accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')  # '-' for stdout
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Error log
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')  # '-' for stderr
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')  # debug, info, warning, error, critical

# Disable access log for health checks (optional)
# access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s' if '%(r)s'.find('/health') == -1 else None

# ==================== Process Naming ====================

# Set process title
def on_starting(server):
    """Called just before the master process is initialized."""
    server.log.info("Starting Vision Inspection Backend")
    server.log.info(f"Worker class: {worker_class}")
    server.log.info(f"Workers: {workers}")
    server.log.info(f"Bind: {bind}")

def on_reload(server):
    """Called on reload."""
    server.log.info("Reloading Vision Inspection Backend")

def when_ready(server):
    """Called just after the server is started."""
    server.log.info("Vision Inspection Backend is ready. Waiting for requests...")

def on_exit(server):
    """Called on server exit."""
    server.log.info("Vision Inspection Backend shutting down")

def worker_int(worker):
    """Called when a worker receives the SIGINT or SIGQUIT signal."""
    worker.log.info(f"Worker {worker.pid} received SIGINT/SIGQUIT")

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info(f"Worker {worker.pid} received SIGABRT - exiting")

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    server.log.info(f"Worker {worker.pid} spawned")

def post_worker_init(worker):
    """Called just after a worker has initialized the application."""
    worker.log.info(f"Worker {worker.pid} initialized")

def worker_exit(server, worker):
    """Called when a worker is exiting."""
    server.log.info(f"Worker {worker.pid} exiting")

# ==================== SSL Configuration (Optional) ====================

# For HTTPS support (uncomment and configure):
# keyfile = '/path/to/keyfile.key'
# certfile = '/path/to/certfile.crt'
# ca_certs = '/path/to/ca_certs.pem'  # Optional
# cert_reqs = 0  # ssl.CERT_NONE
# ssl_version = 2  # ssl.PROTOCOL_TLSv1_2
# ciphers = 'TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256'

# ==================== Security ====================

# Limit request line size
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

# ==================== Performance Tuning ====================

# Preload application (saves memory but harder to reload)
preload_app = False  # Set to True for production if you don't need hot reload

# Enable threading (eventlet/gevent already handle concurrency)
threads = 1  # Not needed with eventlet/gevent

# Forwarded allow IPs (for proxies like nginx)
forwarded_allow_ips = '*'  # Be more specific in production: '127.0.0.1,10.0.0.0/8'

# ==================== Development Overrides ====================

# Override for development
if os.environ.get('FLASK_ENV') == 'development':
    reload = True  # Auto-reload on code changes
    loglevel = 'debug'
    accesslog = '-'
    errorlog = '-'

# ==================== Environment-Specific Settings ====================

# Load from environment
if os.environ.get('GUNICORN_RELOAD') == 'true':
    reload = True

if os.environ.get('GUNICORN_PRELOAD') == 'true':
    preload_app = True

# ==================== Notes ====================
"""
Production Deployment Checklist:
---------------------------------

1. Set appropriate environment variables:
   export GUNICORN_WORKERS=1  # For SocketIO, 1 is usually best
   export GUNICORN_BIND=0.0.0.0:5000
   export GUNICORN_LOG_LEVEL=info

2. Use systemd or supervisor for process management:
   systemctl start vision-inspection-backend

3. Use nginx as reverse proxy:
   - Handle SSL termination
   - Load balancing (if multiple instances)
   - Static file serving
   - DDoS protection

4. Monitor with:
   - Prometheus + Grafana
   - Datadog / New Relic
   - Custom health checks

5. Performance tips:
   - Keep workers=1 for SocketIO
   - Use nginx upstream for load balancing
   - Enable preload_app for production
   - Monitor memory usage
   - Set appropriate timeouts

6. Security:
   - Use HTTPS (configure SSL or use nginx)
   - Set strong SECRET_KEY
   - Configure firewall rules
   - Regular security updates
   - Limit forwarded_allow_ips

Example nginx configuration:
---------------------------

upstream vision_backend {
    server 127.0.0.1:5000;
}

server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://vision_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
"""
