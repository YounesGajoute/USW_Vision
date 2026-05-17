@echo off
REM Vision Inspection System - Start Script (Windows)
REM Runs both frontend and backend together

echo ==================================
echo Vision Inspection System
echo Starting Frontend and Backend...
echo ==================================
echo.

REM Check if backend directory exists
if not exist "backend\" (
    echo [ERROR] backend directory not found
    exit /b 1
)

REM Start backend in new window
echo [BACKEND] Starting backend server...
start "Backend Server" cmd /k "cd backend && python app.py"

REM Wait a moment
timeout /t 3 /nobreak >nul

REM Start frontend in new window
echo [FRONTEND] Starting frontend development server...
start "Frontend Dev Server" cmd /k "npm run dev"

echo.
echo ==================================
echo Application Started Successfully
echo ==================================
echo.
echo Frontend: http://localhost:3000
echo Backend:  http://localhost:5000
echo.
echo Close the terminal windows to stop services
echo.

pause
