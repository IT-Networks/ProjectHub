@echo off
REM ProjectHub Production Start Script (Backend + Frontend)

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%CD%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"

echo.
echo ==========================================
echo ProjectHub Production Start
echo ==========================================
echo.

REM Build Frontend
echo [1/3] Building Frontend...
cd /d "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed!
    exit /b 1
)
echo [OK] Frontend built
echo.

REM Check Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found!
    exit /b 1
)

REM Start Backend
echo [2/3] Starting Backend (Port 3001)...
cd /d "%BACKEND_DIR%"
if not exist "venv" (
    echo   Creating Python venv...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -q -r requirements.txt >nul 2>&1

start "ProjectHub Backend" cmd /k "python run.py"
timeout /t 3 /nobreak
echo [OK] Backend started (serving Frontend + API)
echo.

echo ==========================================
echo ProjectHub is running!
echo ==========================================
echo.
echo URLs:
echo   ProjectHub:  http://localhost:3001
echo   API Docs:    http://localhost:3001/docs
echo.
echo Close terminal window to stop service.
echo.
pause
