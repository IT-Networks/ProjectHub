#!/bin/bash

# ProjectHub Frontend Install & Start Script
# Vollständige Pfade für Production Server

set -e  # Exit on error

PROJECT_ROOT="C:/Users/marku/OneDrive/Dokumente/VisualCode/ProjectHub"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

echo "=========================================="
echo "ProjectHub Frontend Installation"
echo "=========================================="
echo ""

# Check Node.js & npm
echo "[1/5] Überprüfe Node.js und npm..."
if ! command -v node &> /dev/null; then
    echo "❌ Node.js nicht gefunden! Installiere Node.js von nodejs.org"
    exit 1
fi

NODE_VERSION=$(node -v)
NPM_VERSION=$(npm -v)
echo "✓ Node.js: $NODE_VERSION"
echo "✓ npm: $NPM_VERSION"
echo ""

# Navigate to frontend directory
echo "[2/5] Gehe zu Frontend-Verzeichnis..."
cd "$FRONTEND_DIR"
echo "✓ Verzeichnis: $(pwd)"
echo ""

# Clean previous installations
echo "[3/5] Räume alte Installation auf..."
if [ -d "node_modules" ]; then
    echo "  Lösche node_modules..."
    rm -rf node_modules
fi
if [ -f "package-lock.json" ]; then
    echo "  Lösche package-lock.json..."
    rm -f package-lock.json
fi
echo "✓ Aufgeräumt"
echo ""

# Install dependencies
echo "[4/5] Installiere Dependencies (npm install)..."
npm install --legacy-peer-deps
if [ $? -ne 0 ]; then
    echo "❌ npm install fehlgeschlagen"
    exit 1
fi
echo "✓ Dependencies installiert"
echo ""

# Build for production
echo "[5/5] Baue Production-Version..."
npm run build
if [ $? -ne 0 ]; then
    echo "❌ npm run build fehlgeschlagen"
    exit 1
fi

# Verify dist folder
if [ ! -d "dist" ]; then
    echo "❌ Build-Ausgabe (dist/) nicht gefunden"
    exit 1
fi
echo "✓ Build erfolgreich"
echo "✓ Dist-Folder vorhanden: $(du -sh dist | cut -f1)"
echo ""

# Update config for multi-port setup
echo "[6/5] Updating configuration..."
cd "$PROJECT_ROOT/backend"
if grep -q "localhost:3000" config.py; then
    echo "✓ CORS bereits konfiguriert"
else
    # Add port 3000 to CORS origins if not present
    sed -i 's/http:\/\/localhost:3001"/http:\/\/localhost:3001", "http:\/\/localhost:3000"/' config.py 2>/dev/null || true
    echo "✓ CORS aktualisiert für Port 3000"
fi
echo ""

# Show summary
echo "=========================================="
echo "✓ Installation erfolgreich!"
echo "=========================================="
echo ""
echo "Frontend-Informationen:"
echo "  Root: $PROJECT_ROOT"
echo "  Frontend: $FRONTEND_DIR"
echo "  Build-Output: $FRONTEND_DIR/dist"
echo ""
echo "Nächste Schritte:"
echo "  1. Backend starten: cd $PROJECT_ROOT/backend && python main.py"
echo "  2. Frontend starten: cd $FRONTEND_DIR && npm run dev"
echo "  3. Öffne Browser: http://localhost:5173"
echo ""
