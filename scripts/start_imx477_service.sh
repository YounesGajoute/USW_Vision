#!/bin/bash
# Start IMX477 Camera Service
# Raspberry Pi 5 Optimized

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

echo "🚀 Starting IMX477 Camera Service..."

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "⚠️  Warning: Not running on Raspberry Pi hardware"
    echo "   Camera functionality will be limited"
fi

# Activate virtual environment if it exists
if [ -d "$PROJECT_ROOT/venv" ]; then
    echo "📦 Activating virtual environment..."
    source "$PROJECT_ROOT/venv/bin/activate"
else
    echo "⚠️  Virtual environment not found. Using system Python."
fi

# Check for required packages
echo "🔍 Checking dependencies..."
python3 -c "import picamera2" 2>/dev/null || {
    echo "❌ Error: picamera2 not installed"
    echo "   Install with: pip install picamera2"
    exit 1
}

python3 -c "import cv2" 2>/dev/null || {
    echo "❌ Error: OpenCV not installed"
    echo "   Install with: pip install opencv-python"
    exit 1
}

python3 -c "import fastapi" 2>/dev/null || {
    echo "❌ Error: FastAPI not installed"
    echo "   Install with: pip install -r backend/requirements_imx477.txt"
    exit 1
}

# Set performance governor to 'performance' for maximum speed
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    echo "⚡ Setting CPU governor to performance mode..."
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo performance | sudo tee $cpu > /dev/null 2>&1 || true
    done
fi

# Increase CMA memory for camera (if not already set)
CMA_SIZE=$(vcgencmd get_mem cma | cut -d= -f2)
echo "📸 Camera Memory Allocation (CMA): $CMA_SIZE"
if [ "${CMA_SIZE//[!0-9]/}" -lt 512 ]; then
    echo "⚠️  Warning: CMA memory is less than 512MB"
    echo "   For 4K capture, add 'dtoverlay=vc4-kms-v3d,cma-512' to /boot/config.txt"
fi

# Check camera connection
if ! vcgencmd get_camera 2>/dev/null | grep -q "detected=1"; then
    echo "❌ Error: Camera not detected!"
    echo "   Check camera connection and enable camera interface"
    exit 1
fi

echo "✅ Camera detected and ready"

# Set environment variables for optimization
export OPENCV_OPENCL_RUNTIME=""  # Disable OpenCL (not needed on Pi)
export OMP_NUM_THREADS=4          # Use all 4 cores
export OPENBLAS_NUM_THREADS=4
export MKL_NUM_THREADS=4

# Start the service
echo "🎥 Starting IMX477 Camera API service..."
echo "   API will be available at http://localhost:8000"
echo "   Documentation at http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the service"
echo ""

cd "$BACKEND_DIR"
python3 -m uvicorn imx477_camera:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    --access-log

# Cleanup on exit
trap "echo ''; echo '🛑 Stopping IMX477 Camera Service...'; exit 0" INT TERM

