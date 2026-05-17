#!/bin/bash

# Vision Inspection System - Start Script
# Runs both frontend and backend together

echo "=================================="
echo "Vision Inspection System"
echo "Starting Frontend and Backend..."
echo "=================================="
echo ""

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if backend directory exists
if [ ! -d "backend" ]; then
    echo -e "${RED}Error: backend directory not found${NC}"
    exit 1
fi

# Check if Python is available
if ! command -v python &> /dev/null; then
    echo -e "${RED}Error: Python not found${NC}"
    exit 1
fi

# Check if npm is available
if ! command -v npm &> /dev/null; then
    echo -e "${RED}Error: npm not found${NC}"
    exit 1
fi

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $(jobs -p) 2>/dev/null
    wait
    echo "All processes stopped"
    exit 0
}

# Trap CTRL+C
trap cleanup SIGINT SIGTERM

# Start backend
echo -e "${GREEN}[BACKEND]${NC} Starting backend server..."
cd backend
python app.py &
BACKEND_PID=$!
cd ..

# Wait a moment for backend to start
sleep 2

# Check if backend is still running
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}[ERROR] Backend failed to start${NC}"
    exit 1
fi

echo -e "${GREEN}[BACKEND]${NC} Backend running (PID: $BACKEND_PID)"

# Start frontend
echo -e "${CYAN}[FRONTEND]${NC} Starting frontend development server..."
npm run dev &
FRONTEND_PID=$!

echo -e "${CYAN}[FRONTEND]${NC} Frontend running (PID: $FRONTEND_PID)"

echo ""
echo "=================================="
echo "✓ Application Started Successfully"
echo "=================================="
echo ""
echo -e "${CYAN}Frontend:${NC} http://localhost:3000"
echo -e "${GREEN}Backend:${NC}  http://localhost:5000"
echo ""
echo "Press CTRL+C to stop all services"
echo ""

# Wait for all background processes
wait
