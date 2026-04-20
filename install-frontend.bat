@echo off
REM ProjectHub Frontend Installation Script for Windows
REM Usage: install-frontend.bat

setlocal enabledelayedexpansion

set "PROJECT_ROOT=%CD%"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"

echo.
echo ==========================================
echo ProjectHub Frontend Installation
echo ==========================================
echo.

REM Check Node.js
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js not found! Install from nodejs.org
    exit /b 1
)

for /f "tokens=*" %%i in ('node --version') do set NODE_VERSION=%%i
for /f "tokens=*" %%i in ('npm --version') do set NPM_VERSION=%%i

echo [1/5] Checking Node.js and npm...
echo   Node.js: %NODE_VERSION%
echo   npm: %NPM_VERSION%
echo.

REM Navigate to frontend
echo [2/5] Navigating to frontend directory...
cd /d "%FRONTEND_DIR%"
if errorlevel 1 (
    echo [ERROR] Could not change to frontend directory
    exit /b 1
)
echo   Current: %CD%
echo.

REM Cleanup
echo [3/5] Cleaning up old installation...
if exist "node_modules" (
    echo   Removing node_modules...
    rmdir /s /q "node_modules" 2>nul
)
if exist "package-lock.json" (
    echo   Removing package-lock.json...
    del /q "package-lock.json" 2>nul
)
echo   OK
echo.

REM Install dependencies
echo [4/5] Installing dependencies with npm...
call npm install --legacy-peer-deps
if errorlevel 1 (
    echo [ERROR] npm install failed
    exit /b 1
)
echo.

REM Build
echo [5/6] Building production version...
call npm run build
if errorlevel 1 (
    echo [ERROR] npm run build failed
    exit /b 1
)

if not exist "dist" (
    echo [ERROR] Build output (dist/) not found
    exit /b 1
)

echo [6/6] Verifying configuration...
cd /d "%BACKEND_DIR%"
findstr "localhost:3000" config.py >nul
if errorlevel 1 (
    echo   Updating CORS for port 3000...
    powershell -Command "(Get-Content config.py) -replace 'localhost:3001\"', 'localhost:3001\", \"http://localhost:3000\"' | Set-Content config.py"
) else (
    echo   CORS already configured
)

echo.
echo ==========================================
echo INSTALLATION SUCCESSFUL!
echo ==========================================
echo.
echo Frontend Information:
echo   Root: %PROJECT_ROOT%
echo   Frontend: %FRONTEND_DIR%
echo   Build Output: %FRONTEND_DIR%\dist
echo.
echo Next Steps:
echo   1. Backend: cd backend ^&^& python main.py
echo   2. Frontend: cd frontend ^&^& npm run dev
echo   3. Browser: http://localhost:5173
echo.
pause
