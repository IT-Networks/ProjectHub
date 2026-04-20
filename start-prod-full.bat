@echo off
REM ProjectHub Production Start Script (Full Setup with AI-Assist)

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%CD%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"

echo.
echo ==========================================
echo ProjectHub Production Start (Full)
echo ==========================================
echo.

REM Check if dist exists
if not exist "%FRONTEND_DIR%\dist" (
    echo [ERROR] Frontend not built! Run first:
    echo   start-prod.bat
    exit /b 1
)

REM Check Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found!
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo [READY] %PYTHON_VERSION%
echo.

REM Start Backend
echo [1/2] Starting Backend (FastAPI on port 3001)...
cd /d "%BACKEND_DIR%"
if not exist "requirements.txt" (
    echo [ERROR] Backend requirements.txt not found
    exit /b 1
)

REM Check venv
if not exist "venv" (
    echo   Creating Python venv...
    python -m venv venv
)

REM Activate venv and start backend
call venv\Scripts\activate.bat
pip install -q -r requirements.txt >nul 2>&1

echo   Starting backend (run.py handles socket management)...
start "ProjectHub Backend" cmd /k "python run.py"
echo [OK] Backend started on port 3001
timeout /t 3 /nobreak
echo.

echo ==========================================
echo ProjectHub is running!
echo ==========================================
echo.
echo URLs:
echo   ProjectHub Frontend:  http://localhost:3001
echo   ProjectHub API:       http://localhost:3001/api
echo   ProjectHub API Docs:  http://localhost:3001/docs
echo.
echo Note: AI-Assist server should be running on port 8000
echo.
echo Both services are running in separate terminals.
echo Close terminal windows to stop services.
echo.
pause
