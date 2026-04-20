#!/bin/bash

# ProjectHub Production Start Script (Full Setup)
# Startet Backend + Frontend für Production

set -e

# Get the directory where this script is located (relative path friendly)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

echo "=========================================="
echo "ProjectHub Production Start (Full)"
echo "=========================================="
echo ""

# Check if dist exists
if [ ! -d "$FRONTEND_DIR/dist" ]; then
    echo "❌ Frontend nicht gebaut! Führe zuerst aus:"
    echo "   bash start-prod-full.sh"
    exit 1
fi

# Check Python
if ! command -v python &> /dev/null; then
    echo "❌ Python nicht gefunden!"
    exit 1
fi

echo "✓ Python: $(python --version)"
echo ""

# Start Backend
echo "[1/2] Starte Backend (FastAPI on Port 3001)..."
cd "$BACKEND_DIR"

if [ ! -f "requirements.txt" ]; then
    echo "❌ Backend requirements.txt nicht gefunden"
    exit 1
fi

# Check venv
if [ ! -d "venv" ]; then
    echo "  Erstelle Python venv..."
    python -m venv venv
fi

# Activate venv (Git Bash compatible)
if [ -f "venv/Scripts/activate" ]; then
    source venv/Scripts/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Install requirements
if [ -f "requirements.txt" ]; then
    echo "  Installiere Dependencies..."
    pip install -q -r requirements.txt 2>/dev/null || true
fi

echo "  Starte Backend auf Port 3001..."
# Start backend in background
python main.py &
BACKEND_PID=$!
echo "✓ Backend PID: $BACKEND_PID"
echo ""

# Wait for backend to start
sleep 3

# Start Frontend
echo "[2/2] Starte Frontend (Static Server on Port 3000)..."
cd "$FRONTEND_DIR"

# Check if Python http.server is available
python -m http.server 3000 --directory dist &
FRONTEND_PID=$!
echo "✓ Frontend static server PID: $FRONTEND_PID"
echo ""

# Show info
echo "=========================================="
echo "✓ ProjectHub läuft!"
echo "=========================================="
echo ""
echo "URLs:"
echo "  ProjectHub Frontend: http://localhost:3000"
echo "  ProjectHub Backend API: http://localhost:3001"
echo "  ProjectHub Backend Docs: http://localhost:3001/docs"
echo ""
echo "Hinweis: AI-Assist Server sollte auf Port 8000 laufen"
echo ""
echo "Logs:"
echo "  Backend läuft mit PID $BACKEND_PID"
echo "  Frontend läuft mit PID $FRONTEND_PID"
echo ""
echo "Zum Stoppen: kill $BACKEND_PID $FRONTEND_PID"
echo ""

# Keep script running
wait
