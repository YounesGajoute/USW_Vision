#!/bin/bash
# Vision Inspection System - Deployment Script

set -e  # Exit on error

echo "=== Vision Inspection System Deployment ==="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running as correct user
if [ "$EUID" -eq 0 ]; then 
   echo -e "${RED}Do not run this script as root${NC}"
   exit 1
fi

# Get project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="$PROJECT_DIR/venv"

echo -e "${YELLOW}Project directory: $PROJECT_DIR${NC}"
echo ""

# Step 1: Pull latest code (if using git)
echo -e "${GREEN}Step 1: Checking for updates...${NC}"
if [ -d "$PROJECT_DIR/.git" ]; then
    cd "$PROJECT_DIR"
    git fetch
    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse @{u})
    
    if [ $LOCAL != $REMOTE ]; then
        echo "New version available. Pulling changes..."
        git pull
    else
        echo "Already up to date."
    fi
else
    echo "Not a git repository. Skipping..."
fi
echo ""

# Step 2: Activate virtual environment
echo -e "${GREEN}Step 2: Setting up virtual environment...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo "Virtual environment activated."
echo ""

# Step 3: Install/update dependencies
echo -e "${GREEN}Step 3: Installing dependencies...${NC}"
pip install --upgrade pip
pip install -r "$BACKEND_DIR/requirements.txt"
echo ""

# Step 4: Run database migrations
echo -e "${GREEN}Step 4: Running database migrations...${NC}"
cd "$BACKEND_DIR"
if [ -d "alembic" ]; then
    alembic upgrade head
else
    echo "No migrations directory found. Skipping..."
fi
echo ""

# Step 5: Create backup before deployment
echo -e "${GREEN}Step 5: Creating backup...${NC}"
bash "$BACKEND_DIR/scripts/backup.sh"
echo ""

# Step 6: Run tests (optional)
if [ "$1" == "--skip-tests" ]; then
    echo -e "${YELLOW}Skipping tests...${NC}"
else
    echo -e "${GREEN}Step 6: Running tests...${NC}"
    if [ -d "$BACKEND_DIR/tests" ]; then
        pytest "$BACKEND_DIR/tests" -v || {
            echo -e "${RED}Tests failed! Deployment aborted.${NC}"
            exit 1
        }
    else
        echo "No tests directory found. Skipping..."
    fi
fi
echo ""

# Step 7: Check configuration
echo -e "${GREEN}Step 7: Checking configuration...${NC}"
if [ ! -f "$BACKEND_DIR/.env" ]; then
    echo -e "${YELLOW}Warning: .env file not found!${NC}"
    echo "Please create .env file from .env.example"
    exit 1
fi
echo "Configuration OK."
echo ""

# Step 8: Create necessary directories
echo -e "${GREEN}Step 8: Creating directories...${NC}"
mkdir -p "$BACKEND_DIR/logs"
mkdir -p "$BACKEND_DIR/database"
mkdir -p "$BACKEND_DIR/storage/master_images"
mkdir -p "$BACKEND_DIR/storage/image_history"
mkdir -p "$BACKEND_DIR/storage/backups"
echo "Directories created."
echo ""

# Step 9: Set permissions
echo -e "${GREEN}Step 9: Setting permissions...${NC}"
chmod 750 "$BACKEND_DIR/logs"
chmod 750 "$BACKEND_DIR/database"
chmod 750 "$BACKEND_DIR/storage"
echo "Permissions set."
echo ""

# Step 10: Restart service
echo -e "${GREEN}Step 10: Restarting service...${NC}"
if systemctl is-active --quiet inspection-vision; then
    echo "Stopping service..."
    sudo systemctl stop inspection-vision
    sleep 2
fi

echo "Starting service..."
sudo systemctl start inspection-vision

# Wait for service to start
sleep 3

if systemctl is-active --quiet inspection-vision; then
    echo -e "${GREEN}Service started successfully!${NC}"
else
    echo -e "${RED}Service failed to start!${NC}"
    echo "Check logs with: sudo journalctl -u inspection-vision -n 50"
    exit 1
fi
echo ""

# Step 11: Health check
echo -e "${GREEN}Step 11: Running health check...${NC}"
sleep 2

API_HOST=$(grep API_HOST "$BACKEND_DIR/.env" | cut -d '=' -f2 | tr -d ' ' | tr -d '"')
API_PORT=$(grep API_PORT "$BACKEND_DIR/.env" | cut -d '=' -f2 | tr -d ' ' | tr -d '"')

if [ -z "$API_HOST" ]; then API_HOST="localhost"; fi
if [ -z "$API_PORT" ]; then API_PORT="5000"; fi

HEALTH_URL="http://$API_HOST:$API_PORT/api/v1/health"

if curl -s -f "$HEALTH_URL" > /dev/null; then
    echo -e "${GREEN}Health check passed!${NC}"
    echo "API is responding at: $HEALTH_URL"
else
    echo -e "${YELLOW}Warning: Health check failed or API not reachable${NC}"
    echo "Check service logs for details"
fi
echo ""

# Step 12: Display service status
echo -e "${GREEN}Step 12: Service status${NC}"
sudo systemctl status inspection-vision --no-pager -l
echo ""

echo -e "${GREEN}=== Deployment Complete ===${NC}"
echo ""
echo "Next steps:"
echo "  - Monitor logs: sudo journalctl -u inspection-vision -f"
echo "  - Check status: sudo systemctl status inspection-vision"
echo "  - API endpoint: http://$API_HOST:$API_PORT/api/v1"
echo ""
