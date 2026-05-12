@echo off
echo === ProjectHub starten ===
echo.
echo Backend: http://localhost:5001
echo Frontend (dev): http://localhost:5173
echo.

cd /d "%~dp0backend"
if not exist "venv\Scripts\python.exe" (
    echo Erstelle Python venv...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -q -r requirements.txt
)

start "ProjectHub Backend" cmd /k "call venv\Scripts\activate.bat && python run.py"

timeout /t 2 /nobreak >nul

cd /d "%~dp0frontend"
start "ProjectHub Frontend" cmd /c "npm run dev"

timeout /t 3 /nobreak >nul
start http://localhost:5173

echo.
echo ProjectHub gestartet. Fenster schliessen um zu beenden.
pause
