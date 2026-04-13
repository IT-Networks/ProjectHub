@echo off
echo === ProjectHub Production Mode ===
echo.

cd /d "%~dp0frontend"
echo Building frontend...
call npm run build
echo.

cd /d "%~dp0backend"
echo Starting server on http://localhost:3001
start http://localhost:3001
python -m uvicorn main:app --port 3001 --host 127.0.0.1
