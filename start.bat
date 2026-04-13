@echo off
echo === ProjectHub starten ===
echo.
echo Backend: http://localhost:3001
echo Frontend (dev): http://localhost:5173
echo.

cd /d "%~dp0backend"
start "ProjectHub Backend" cmd /c "python -m uvicorn main:app --port 3001 --host 127.0.0.1"

timeout /t 2 /nobreak >nul

cd /d "%~dp0frontend"
start "ProjectHub Frontend" cmd /c "npm run dev"

timeout /t 3 /nobreak >nul
start http://localhost:5173

echo.
echo ProjectHub gestartet. Fenster schliessen um zu beenden.
pause
