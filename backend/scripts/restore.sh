#!/bin/bash
# Vision Inspection System - Restore Script

set -e  # Exit on error

echo "=== Vision Inspection System Restore ==="
echo ""

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

# Check if backup file provided
if [ -z "$1" ]; then
    echo -e "${RED}Error: No backup file specified${NC}"
    echo "Usage: $0 <backup_file.tar.gz>"
    echo ""
    echo "Available backups:"
    ls -lh "$(dirname "$0")/../storage/backups/"backup_*.tar.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo -e "${RED}Error: Backup file not found: $BACKUP_FILE${NC}"
    exit 1
fi

# Get project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
RESTORE_DIR="/tmp/vision_restore_$$"

echo -e "${YELLOW}Warning: This will overwrite current data!${NC}"
echo "Backup file: $BACKUP_FILE"
echo ""
read -p "Are you sure you want to restore? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Restore cancelled."
    exit 0
fi

echo ""

# Step 1: Create backup of current state
echo -e "${GREEN}1. Creating backup of current state...${NC}"
bash "$(dirname "$0")/backup.sh"

# Step 2: Stop service
echo -e "${GREEN}2. Stopping service...${NC}"
if systemctl is-active --quiet inspection-vision; then
    sudo systemctl stop inspection-vision
    echo "   Service stopped"
else
    echo "   Service not running"
fi

# Step 3: Extract backup
echo -e "${GREEN}3. Extracting backup...${NC}"
mkdir -p "$RESTORE_DIR"
tar -xzf "$BACKUP_FILE" -C "$RESTORE_DIR"

BACKUP_NAME=$(basename "$BACKUP_FILE" .tar.gz)
EXTRACT_DIR="$RESTORE_DIR/$BACKUP_NAME"

if [ ! -d "$EXTRACT_DIR" ]; then
    echo -e "${RED}Error: Invalid backup file structure${NC}"
    rm -rf "$RESTORE_DIR"
    exit 1
fi

echo "   Backup extracted"

# Step 4: Display backup info
if [ -f "$EXTRACT_DIR/manifest.txt" ]; then
    echo ""
    echo "=== Backup Information ==="
    cat "$EXTRACT_DIR/manifest.txt"
    echo "=========================="
    echo ""
    read -p "Continue with restore? (yes/no): " CONTINUE
    if [ "$CONTINUE" != "yes" ]; then
        echo "Restore cancelled."
        rm -rf "$RESTORE_DIR"
        exit 0
    fi
fi

# Step 5: Restore database
echo -e "${GREEN}4. Restoring database...${NC}"
if [ -f "$EXTRACT_DIR/vision.db" ]; then
    cp "$EXTRACT_DIR/vision.db" "$BACKEND_DIR/database/vision.db"
    echo "   Database restored"
else
    echo -e "${YELLOW}   Warning: No database in backup${NC}"
fi

# Step 6: Restore master images
echo -e "${GREEN}5. Restoring master images...${NC}"
if [ -d "$EXTRACT_DIR/master_images" ]; then
    mkdir -p "$BACKEND_DIR/storage/master_images"
    cp -r "$EXTRACT_DIR/master_images/"* "$BACKEND_DIR/storage/master_images/" 2>/dev/null || true
    IMAGE_COUNT=$(find "$BACKEND_DIR/storage/master_images" -type f 2>/dev/null | wc -l)
    echo "   $IMAGE_COUNT images restored"
else
    echo -e "${YELLOW}   Warning: No master images in backup${NC}"
fi

# Step 7: Restore configuration (optional)
echo -e "${GREEN}6. Restoring configuration...${NC}"
if [ -f "$EXTRACT_DIR/.env" ]; then
    read -p "   Restore .env file? (yes/no): " RESTORE_ENV
    if [ "$RESTORE_ENV" == "yes" ]; then
        cp "$EXTRACT_DIR/.env" "$BACKEND_DIR/.env"
        echo "   Configuration restored"
    else
        echo "   Configuration restore skipped"
    fi
fi

# Step 8: Set permissions
echo -e "${GREEN}7. Setting permissions...${NC}"
chmod 644 "$BACKEND_DIR/database/vision.db" 2>/dev/null || true
chmod -R 755 "$BACKEND_DIR/storage/master_images" 2>/dev/null || true
echo "   Permissions set"

# Step 9: Cleanup
echo -e "${GREEN}8. Cleaning up...${NC}"
rm -rf "$RESTORE_DIR"
echo "   Temporary files removed"

# Step 10: Start service
echo -e "${GREEN}9. Starting service...${NC}"
sudo systemctl start inspection-vision
sleep 3

if systemctl is-active --quiet inspection-vision; then
    echo -e "${GREEN}   Service started successfully${NC}"
else
    echo -e "${RED}   Error: Service failed to start${NC}"
    echo "   Check logs: sudo journalctl -u inspection-vision -n 50"
    exit 1
fi

echo ""
echo -e "${GREEN}=== Restore Complete ===${NC}"
echo "Service is running. Verify functionality:"
echo "  - Check service status: sudo systemctl status inspection-vision"
echo "  - Monitor logs: sudo journalctl -u inspection-vision -f"
echo ""
