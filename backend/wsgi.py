"""
WSGI Entry Point for Production Deployment
==========================================

This module serves as the entry point for WSGI servers like Gunicorn.
It creates the Flask application instance with SocketIO support.

Usage with Gunicorn:
    gunicorn -c gunicorn_config.py wsgi:app

Environment Variables:
    CONFIG_PATH: Path to configuration file (default: config.yaml)
    FLASK_ENV: Flask environment (production, development)
    SECRET_KEY: Application secret key for sessions
"""

import os
import sys

# Add backend directory to Python path
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

# Import application factory
from app import create_app, socketio

# Get configuration path from environment
config_path = os.environ.get('CONFIG_PATH', 'config.yaml')

# Resolve relative config path
if not os.path.isabs(config_path):
    config_path = os.path.join(backend_dir, config_path)

# Create Flask application
app = create_app(config_path)

# Export both app and socketio for Gunicorn
# Gunicorn will use 'app' as the WSGI application
# SocketIO will handle WebSocket connections through the WSGI interface

# For Gunicorn with eventlet:
# The socketio object wraps the Flask app and handles both HTTP and WebSocket

if __name__ == '__main__':
    # This block is for development/testing only
    # In production, use: gunicorn -c gunicorn_config.py wsgi:app
    import yaml
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    host = config.get('api', {}).get('host', '0.0.0.0')
    port = config.get('api', {}).get('port', 5000)
    
    print(f"Running in development mode on {host}:{port}")
    print("For production, use: gunicorn -c gunicorn_config.py wsgi:app")
    
    socketio.run(app, host=host, port=port, debug=False)
