#!/bin/bash
# Vision Inspection System - Backup Script

set -e  # Exit on error

echo "=== Vision Inspection System Backup ==="
echo ""

# Get project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
BACKUP_DIR="$BACKEND_DIR/storage/backups"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Generate timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="backup_$TIMESTAMP"
BACKUP_PATH="$BACKUP_DIR/$BACKUP_NAME"

echo "Creating backup: $BACKUP_NAME"
echo ""

# Create temporary backup directory
mkdir -p "$BACKUP_PATH"

# Step 1: Backup database
echo "1. Backing up database..."
if [ -f "$BACKEND_DIR/database/vision.db" ]; then
    cp "$BACKEND_DIR/database/vision.db" "$BACKUP_PATH/vision.db"
    echo "   Database backed up"
else
    echo "   Warning: Database file not found"
fi

# Step 2: Backup master images
echo "2. Backing up master images..."
if [ -d "$BACKEND_DIR/storage/master_images" ]; then
    mkdir -p "$BACKUP_PATH/master_images"
    cp -r "$BACKEND_DIR/storage/master_images/"* "$BACKUP_PATH/master_images/" 2>/dev/null || true
    IMAGE_COUNT=$(find "$BACKUP_PATH/master_images" -type f 2>/dev/null | wc -l)
    echo "   $IMAGE_COUNT images backed up"
else
    echo "   Warning: Master images directory not found"
fi

# Step 3: Backup configuration
echo "3. Backing up configuration..."
if [ -f "$BACKEND_DIR/.env" ]; then
    cp "$BACKEND_DIR/.env" "$BACKUP_PATH/.env"
    echo "   Configuration backed up"
fi

if [ -f "$BACKEND_DIR/config.yaml" ]; then
    cp "$BACKEND_DIR/config.yaml" "$BACKUP_PATH/config.yaml" 2>/dev/null || true
fi

# Step 4: Create backup manifest
echo "4. Creating backup manifest..."
cat > "$BACKUP_PATH/manifest.txt" << EOF
Vision Inspection System Backup
Created: $(date)
Hostname: $(hostname)

Contents:
$(ls -lh "$BACKUP_PATH")

Database size: $(du -h "$BACKUP_PATH/vision.db" 2>/dev/null | cut -f1 || echo "N/A")
Master images: $IMAGE_COUNT files
EOF

echo "   Manifest created"

# Step 5: Compress backup
echo "5. Compressing backup..."
cd "$BACKUP_DIR"
tar -czf "${BACKUP_NAME}.tar.gz" "$BACKUP_NAME"
rm -rf "$BACKUP_NAME"

BACKUP_SIZE=$(du -h "${BACKUP_NAME}.tar.gz" | cut -f1)
echo "   Backup compressed: ${BACKUP_SIZE}"

# Step 6: Cleanup old backups (keep last 30 days)
echo "6. Cleaning up old backups..."
find "$BACKUP_DIR" -name "backup_*.tar.gz" -type f -mtime +30 -delete
REMAINING=$(ls -1 "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | wc -l)
echo "   $REMAINING backups retained"

echo ""
echo "=== Backup Complete ==="
echo "Backup file: $BACKUP_DIR/${BACKUP_NAME}.tar.gz"
echo "Size: $BACKUP_SIZE"
echo ""
