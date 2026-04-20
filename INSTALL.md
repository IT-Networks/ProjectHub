# ProjectHub Installation & Deployment Guide

## Quick Start

### 1. Frontend Installation

**Windows:**
```batch
install-frontend.bat
```

**Linux/Mac:**
```bash
bash install-frontend.sh
```

**What it does:**
- ✓ Checks Node.js & npm
- ✓ Cleans old installation (node_modules, package-lock.json)
- ✓ Installs all npm dependencies
- ✓ Builds production-ready bundle (`dist/` folder)
- ✓ Updates CORS configuration for port 3000

**Result:** ~1.5 MB minified bundle in `frontend/dist/`

### 2. Complete Stack Start (RECOMMENDED)

Start all services at once from the parent directory:

**Windows:**
```batch
start-all.bat
```

**Linux/Mac:**
```bash
bash start-all.sh
```

**What it does:**
- ✓ Starts AI-Assist Server on port 8000
- ✓ Starts ProjectHub Backend (FastAPI) on port 3001
- ✓ Starts ProjectHub Frontend (Static) on port 3000
- ✓ Opens separate terminal windows for each service
- ✓ Frontend automatically connects to Backend
- ✓ Backend automatically connects to AI-Assist

**Access:**
- Frontend: http://localhost:3000 (Main UI)
- ProjectHub API: http://localhost:3001/docs
- AI-Assist: http://localhost:8000/docs

### 3. Individual Service Start (ProjectHub only)

**Windows:**
```batch
ProjectHub\start-prod-full.bat
```

**Linux/Mac:**
```bash
bash ProjectHub/start-prod-full.sh
```

**What it does:**
- ✓ Starts ProjectHub Backend on port 3001
- ✓ Starts ProjectHub Frontend on port 3000
- ⚠️ Requires AI-Assist running separately

---

## Manual Setup

### Frontend Only

```bash
cd frontend
npm install --legacy-peer-deps
npm run build
# Output: frontend/dist/
```

### Start Frontend (Development)

```bash
cd frontend
npm run dev
# Runs on http://localhost:5173 with hot reload
```

### Start Frontend (Production)

```bash
cd frontend
# Static server on port 3001
python -m http.server 3001 --directory dist

# OR with Node.js
npx http-server dist -p 3001
```

### Backend Setup

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
python main.py
# Runs on http://localhost:8000
```

---

## System Requirements

- **Node.js:** v22.19.0 or higher
- **npm:** v10.9.3 or higher
- **Python:** v3.9 or higher
- **Disk Space:** ~500 MB (dependencies) + 50 MB (built bundle)

---

## File Structure

```
VisualCode/
├── start-all.sh             # MASTER: Start all services (Linux/Mac)
├── start-all.bat            # MASTER: Start all services (Windows)
│
├── AI-Assist/               # AI-Assist Server (Port 8000)
│   └── AI-Assist/
│       └── main.py
│
└── ProjectHub/              # Project Management UI
    ├── install-frontend.sh      # Linux/Mac install
    ├── install-frontend.bat     # Windows install
    ├── start-prod-full.sh       # Linux/Mac start (ProjectHub only)
    ├── start-prod-full.bat      # Windows start (ProjectHub only)
    ├── frontend-proxy-final.py  # (Legacy, not needed)
    ├── INSTALL.md               # This file
    ├── INSTALL_SUMMARY.txt      # Quick summary
    ├── VERSION
    │
    ├── frontend/                # React + Vite (Port 3000)
    │   ├── src/                 # Source code
    │   ├── dist/                # Production build
    │   ├── node_modules/        # Dependencies
    │   ├── package.json
    │   └── tsconfig.app.json
    │
    └── backend/                 # FastAPI (Port 3001)
        ├── main.py              # Entry point
        ├── config.py            # Configuration
        ├── models/              # SQLAlchemy models
        ├── routers/             # API endpoints
        ├── services/            # Business logic
        ├── requirements.txt
        └── venv/                # Python venv (created if needed)
```

---

## Troubleshooting

### "Node.js not found"
- Install from https://nodejs.org/en/download
- Verify: `node --version`, `npm --version`

### "npm install fails"
```bash
cd frontend
rm -rf node_modules package-lock.json
npm install --legacy-peer-deps
```

### "Port 3001 already in use"
```bash
# Windows: Find process on port 3001
netstat -ano | findstr :3001
taskkill /PID <PID> /F

# Linux/Mac:
lsof -i :3001
kill -9 <PID>
```

### "Frontend shows blank page"
- Check backend is running: http://localhost:8000
- Check browser console for errors (F12)
- Clear cache: Ctrl+Shift+Delete
- Verify `frontend/dist/` exists

### "Backend connection error"
- Ensure backend is running: `python main.py`
- Check if port 8000 is free: `netstat -ano | findstr :8000`
- Check AI-Assist is configured: `backend/config.py`

---

## Production Deployment

### Option 1: Docker (Recommended)
```bash
# Use existing start-prod-full scripts
# Both services run in separate processes
```

### Option 2: Reverse Proxy (Nginx/Apache)
```nginx
# Serve frontend from http://localhost:3001
server {
    listen 80;
    location / {
        proxy_pass http://localhost:3001;
    }
    location /api/ {
        proxy_pass http://localhost:8000;
    }
}
```

### Option 3: PM2 (Process Manager)
```bash
npm install -g pm2

# ecosystem.config.js
module.exports = {
  apps: [
    {
      name: "projecthub-backend",
      script: "python",
      args: "main.py",
      cwd: "./backend",
      watch: false
    },
    {
      name: "projecthub-frontend",
      script: "npx",
      args: "http-server dist -p 3001",
      cwd: "./frontend",
      watch: false
    }
  ]
};

pm2 start ecosystem.config.js
```

---

## Configuration

### Backend (backend/config.py)
```python
# AI-Assist connection
ai_assist_url = "http://localhost:8000"  # Adjust if different
ai_assist_timeout = 30

# Database
db_path = "./data/projecthub.db"

# Server
port = 8000
host = "127.0.0.1"
```

### Frontend Environment
Create `frontend/.env` if needed:
```env
VITE_API_URL=http://localhost:8000
VITE_API_TIMEOUT=30000
```

---

## Version History

- **v1.0.0** (2026-04-14): Initial release with dashboard, kanban, knowledge base
  - TypeScript build fixes (ignoreDeprecations: "6.0")
  - Removed tsc type-checking from build (vite-only)

---

## Support

For issues:
1. Check the logs in terminal windows
2. Verify all requirements are installed
3. Ensure ports 3001 (frontend) and 8000 (backend) are available
4. Check memory and disk space

---

**Last Updated:** 2026-04-18  
**Verified:** Node.js v22.19.0, Python 3.x, Windows 11
