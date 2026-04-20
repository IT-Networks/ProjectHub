#!/usr/bin/env python3
"""
ProjectHub Frontend Proxy Server
Serves static frontend from dist/ and proxies /api/* to backend
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import urllib.request
import urllib.error
import json
import os

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
BACKEND_URL = "http://localhost:3001"
FRONTEND_PORT = 3000

class ProxyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_GET(self):
        # API Proxy: /api/* → backend
        if self.path.startswith("/api/"):
            self.proxy_to_backend("GET")
        # Frontend: everything else
        else:
            self.serve_static()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self.proxy_to_backend("POST")
        else:
            super().do_GET()

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self.proxy_to_backend("PUT")
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self.proxy_to_backend("DELETE")
        else:
            self.send_error(405)

    def proxy_to_backend(self, method):
        """Proxy request to backend at localhost:3001"""
        try:
            url = f"{BACKEND_URL}{self.path}"

            # Read body if present
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b""

            # Create request
            req = urllib.request.Request(
                url,
                data=body,
                method=method,
                headers={k: v for k, v in self.headers.items() if k.lower() not in ["host", "connection"]}
            )

            # Forward request
            with urllib.request.urlopen(req, timeout=30) as response:
                self.send_response(response.status)
                for header, value in response.headers.items():
                    if header.lower() not in ["content-encoding"]:
                        self.send_header(header, value)
                self.end_headers()
                self.wfile.write(response.read())

        except urllib.error.URLError as e:
            self.send_error(502, f"Backend unreachable: {e}")
        except Exception as e:
            self.send_error(500, f"Proxy error: {e}")

    def serve_static(self):
        """Serve static files from dist/"""
        # Remove query string
        path = self.path.split("?")[0]

        # Try exact file first
        file_path = FRONTEND_DIR / path.lstrip("/")

        if file_path.exists() and file_path.is_file():
            self.send_file(file_path)
        # Try with index.html for directories
        elif (file_path / "index.html").exists():
            self.send_file(file_path / "index.html")
        # SPA: serve index.html for unknown routes
        elif path != "/" and not path.endswith((".js", ".css", ".svg", ".woff2", ".png", ".jpg")):
            self.send_file(FRONTEND_DIR / "index.html")
        # Not found
        else:
            self.send_error(404)

    def send_file(self, file_path):
        """Send static file with correct content-type"""
        try:
            with open(file_path, "rb") as f:
                content = f.read()

            # Content type mapping
            content_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
                ".woff2": "font/woff2",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".ico": "image/x-icon",
            }

            suffix = Path(file_path).suffix
            content_type = content_types.get(suffix, "application/octet-stream")

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))

            # Cache control
            if suffix in [".html"]:
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            else:
                self.send_header("Cache-Control", "public, max-age=3600")

            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error serving file: {e}")

    def end_headers(self):
        # CORS headers for API
        if self.path.startswith("/api/"):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def log_message(self, format, *args):
        """Custom logging"""
        if isinstance(args[0], str):
            method = args[0].split()[0]
            path = args[0].split()[1]
            code = args[1]
            size = args[2] if len(args) > 2 else "-"
            status = "✓" if code.startswith("2") else "✗"
            print(f"{status} {code} {method:6} {path[:50]:50} ({size})")
        else:
            print(format % args)


def main():
    os.chdir(str(FRONTEND_DIR.parent))

    print("=" * 70)
    print("ProjectHub Frontend Proxy Server")
    print("=" * 70)
    print()
    print(f"Frontend:  http://localhost:{FRONTEND_PORT}/")
    print(f"Backend:   {BACKEND_URL}")
    print(f"Dist Dir:  {FRONTEND_DIR}")
    print()
    print("Routes:")
    print(f"  GET  /api/*        -> {BACKEND_URL}/api/*")
    print(f"  GET  /*            -> dist/index.html (SPA)")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)
    print()

    server_address = ("0.0.0.0", FRONTEND_PORT)
    httpd = HTTPServer(server_address, ProxyHandler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
