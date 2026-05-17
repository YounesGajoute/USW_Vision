#!/bin/bash
# Vision Inspection System - Complete Setup Script for Raspberry Pi

set -e  # Exit on error

echo "==============================================="
echo "  Vision Inspection System Setup"
echo "==============================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo -e "${YELLOW}Warning: Not running on Raspberry Pi. Some features may not work.${NC}"
fi

echo "Step 1: Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

echo ""
echo "Step 2: Installing system dependencies..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-opencv \
    libopencv-dev \
    libatlas-base-dev \
    libjpeg-dev \
    libtiff5-dev \
    libpng-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    libxvidcore-dev \
    libx264-dev \
    sqlite3 \
    git

echo ""
echo "Step 3: Installing Python dependencies..."
cd backend
pip3 install --upgrade pip
pip3 install -r requirements.txt
cd ..

echo ""
echo "Step 4: Installing Node.js and npm..."
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "Node.js already installed"
fi

echo ""
echo "Step 5: Installing frontend dependencies..."
npm install

echo ""
echo "Step 6: Creating directories..."
mkdir -p backend/storage/master_images
mkdir -p backend/storage/image_history
mkdir -p backend/storage/exports
mkdir -p backend/database
mkdir -p logs

echo ""
echo "Step 7: Initializing database..."
cd backend
python3 << 'EOF'
from src.database.db_manager import DatabaseManager
import yaml

with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

db = DatabaseManager(config['database']['path'])
print("Database initialized successfully")
EOF
cd ..

echo ""
echo "Step 8: Setting up systemd services..."

# Create backend service
sudo tee /etc/systemd/system/vision-inspection-backend.service > /dev/null << 'EOF'
[Unit]
Description=Vision Inspection System Backend
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/vision-inspection-system/backend
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
ExecStart=/usr/bin/python3 app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Create frontend service
sudo tee /etc/systemd/system/vision-inspection-frontend.service > /dev/null << 'EOF'
[Unit]
Description=Vision Inspection System Frontend
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/vision-inspection-system/inspection_vision
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
ExecStart=/usr/bin/npm run build && /usr/bin/npm start
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

echo ""
echo "Step 9: Enabling services..."
sudo systemctl enable vision-inspection-backend.service
sudo systemctl enable vision-inspection-frontend.service

echo ""
echo "Step 10: Running hardware tests..."
cd backend
python3 ../scripts/test_hardware.py
cd ..

echo ""
echo "==============================================="
echo -e "${GREEN}  Setup Complete!${NC}"
echo "==============================================="
echo ""
echo "To start the services:"
echo "  sudo systemctl start vision-inspection-backend"
echo "  sudo systemctl start vision-inspection-frontend"
echo ""
echo "To check status:"
echo "  sudo systemctl status vision-inspection-backend"
echo "  sudo systemctl status vision-inspection-frontend"
echo ""
echo "Access the application at:"
echo "  http://localhost:3000"
echo ""
echo "API available at:"
echo "  http://localhost:5000/api"
echo ""
echo "==============================================="

