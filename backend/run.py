#!/usr/bin/env python
"""Production runner that ensures proper module initialization."""

import sys
import logging

# Ensure proper imports are loaded
from routers import sync
from routers import knowledge
from routers import projects
from routers import todos
from routers import notes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

if __name__ == "__main__":
    import uvicorn
    import socket
    import time
    from config import settings
    from main import app

    # Verify sync routes are registered
    sync_routes = [r for r in app.routes if hasattr(r, 'path') and '/sync' in r.path]
    print(f"Registered {len(sync_routes)} sync routes:")
    for route in sync_routes:
        methods = ",".join(sorted(route.methods)) if hasattr(route, "methods") else "N/A"
        print(f"  {methods:15} {route.path}")

    print(f"Total routes: {len(app.routes)}")

    # Use TEST_PORT env var if set (useful for testing when port is in use)
    import os
    port = int(os.environ.get('TEST_PORT', settings.port))

    # Wait for port to be available (handles Windows socket lock)
    retries = 5
    for attempt in range(retries):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            result = sock.connect_ex((settings.host if settings.host != '0.0.0.0' else '127.0.0.1', port))
            sock.close()
            if result != 0:
                break  # Port is available
        except:
            break
        if attempt < retries - 1:
            time.sleep(2)

    uvicorn.run(
        app,
        host=settings.host,
        port=port,
        reload=False,
        access_log=True,
        log_level="info",
        limit_concurrency=100,
        limit_max_requests=10000,
    )
