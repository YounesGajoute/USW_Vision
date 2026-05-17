#!/bin/bash

################################################################################
# Vision Inspection System - Production Startup Script
################################################################################
#
# This script starts the backend using Gunicorn with production settings.
#
# Usage:
#   ./start_production.sh [options]
#
# Options:
#   --workers N         Number of Gunicorn workers (default: 1)
#   --bind HOST:PORT    Bind address (default: 0.0.0.0:5000)
#   --daemon            Run as daemon in background
#   --config FILE       Configuration file path (default: config.yaml)
#   --log-level LEVEL   Log level: debug, info, warning, error (default: info)
#   --help              Show this help message
#
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default settings
WORKERS=1
BIND="0.0.0.0:5000"
DAEMON=false
CONFIG_PATH="$SCRIPT_DIR/config.yaml"
LOG_LEVEL="info"
PID_FILE="/tmp/gunicorn_vision_inspection.pid"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --bind)
            BIND="$2"
            shift 2
            ;;
        --daemon)
            DAEMON=true
            shift
            ;;
        --config)
            CONFIG_PATH="$2"
            shift 2
            ;;
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        --help)
            head -n 20 "$0" | tail -n +3 | sed 's/^# //'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Print header
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Vision Inspection System - Production Server  ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

# Check if virtual environment exists
if [ ! -d "$PROJECT_ROOT/venv" ]; then
    echo -e "${RED}✗ Virtual environment not found!${NC}"
    echo -e "${YELLOW}  Creating virtual environment...${NC}"
    python3 -m venv "$PROJECT_ROOT/venv"
fi

# Activate virtual environment
echo -e "${BLUE}→ Activating virtual environment...${NC}"
source "$PROJECT_ROOT/venv/bin/activate"

# Check if Gunicorn is installed
if ! command -v gunicorn &> /dev/null; then
    echo -e "${RED}✗ Gunicorn not found!${NC}"
    echo -e "${YELLOW}  Installing production dependencies...${NC}"
    pip install -r "$SCRIPT_DIR/requirements.txt"
fi

# Check if config file exists
if [ ! -f "$CONFIG_PATH" ]; then
    echo -e "${RED}✗ Configuration file not found: $CONFIG_PATH${NC}"
    exit 1
fi

# Create required directories
echo -e "${BLUE}→ Creating required directories...${NC}"
mkdir -p "$SCRIPT_DIR/storage/programs"
mkdir -p "$SCRIPT_DIR/storage/images"
mkdir -p "$SCRIPT_DIR/storage/backups"
mkdir -p "$SCRIPT_DIR/logs"
mkdir -p "$SCRIPT_DIR/data"

# Set environment variables
export CONFIG_PATH="$CONFIG_PATH"
export FLASK_ENV=production
export GUNICORN_WORKERS="$WORKERS"
export GUNICORN_BIND="$BIND"
export GUNICORN_LOG_LEVEL="$LOG_LEVEL"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo -e "${YELLOW}⚠  Server already running (PID: $OLD_PID)${NC}"
        echo -e "${YELLOW}   Stop it first with: kill $OLD_PID${NC}"
        exit 1
    else
        echo -e "${YELLOW}⚠  Stale PID file found, removing...${NC}"
        rm -f "$PID_FILE"
    fi
fi

# Display configuration
echo
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Workers:     ${YELLOW}$WORKERS${NC}"
echo -e "  Bind:        ${YELLOW}$BIND${NC}"
echo -e "  Config:      ${YELLOW}$CONFIG_PATH${NC}"
echo -e "  Log Level:   ${YELLOW}$LOG_LEVEL${NC}"
echo -e "  Daemon:      ${YELLOW}$DAEMON${NC}"
echo -e "  PID File:    ${YELLOW}$PID_FILE${NC}"
echo

# Start server
cd "$SCRIPT_DIR"

if [ "$DAEMON" = true ]; then
    echo -e "${GREEN}✓ Starting server in daemon mode...${NC}"
    gunicorn \
        --config gunicorn_config.py \
        --bind "$BIND" \
        --workers "$WORKERS" \
        --worker-class eventlet \
        --daemon \
        --pid "$PID_FILE" \
        --log-level "$LOG_LEVEL" \
        --access-logfile logs/access.log \
        --error-logfile logs/error.log \
        --log-syslog \
        wsgi:app
    
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        echo -e "${GREEN}✓ Server started successfully (PID: $PID)${NC}"
        echo
        echo -e "${BLUE}Management Commands:${NC}"
        echo -e "  Stop:    ${YELLOW}kill $PID${NC}"
        echo -e "  Reload:  ${YELLOW}kill -HUP $PID${NC}"
        echo -e "  Logs:    ${YELLOW}tail -f $SCRIPT_DIR/logs/error.log${NC}"
        echo
        echo -e "${GREEN}API available at: http://$BIND/api${NC}"
    else
        echo -e "${RED}✗ Failed to start server${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓ Starting server in foreground mode...${NC}"
    echo -e "${YELLOW}  Press Ctrl+C to stop${NC}"
    echo
    
    gunicorn \
        --config gunicorn_config.py \
        --bind "$BIND" \
        --workers "$WORKERS" \
        --worker-class eventlet \
        --log-level "$LOG_LEVEL" \
        --access-logfile - \
        --error-logfile - \
        wsgi:app
fi

