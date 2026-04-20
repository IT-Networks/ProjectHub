#!/usr/bin/env python3
"""
ProjectHub Frontend Proxy - Simple & Reliable
Serves static frontend and proxies /api/* to backend
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, urljoin
import requests
import json

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
BACKEND_URL = "http://localhost:3001"
FRONTEND_PORT = 3000

class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Minimal logging"""
        if len(args) > 1:
            method = args[0].split()[0] if isinstance(args[0], str) else "?"
            path = args[0].split()[1][:40] if isinstance(args[0], str) else "?"
            code = str(args[1])
            status = "OK" if code.startswith("2") else code
            print(f"[{status}] {method:6} {path}")

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_PUT(self):
        self.handle_request("PUT")

    def do_DELETE(self):
        self.handle_request("DELETE")

    def handle_request(self, method):
        """Route to API or static files"""
        path = self.path.split("?")[0]

        if path.startswith("/api/"):
            self.proxy_api(method)
        else:
            self.serve_static(path)

    def proxy_api(self, method):
        """Proxy request to backend"""
        try:
            url = f"{BACKEND_URL}{self.path}"

            # Read body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else None

            # Make request
            headers = {k: v for k, v in self.headers.items()
                      if k.lower() not in ["host", "connection"]}

            response = requests.request(
                method,
                url,
                data=body,
                headers=headers,
                timeout=30,
                allow_redirects=False
            )

            # Send response
            self.send_response(response.status_code)
            for header, value in response.headers.items():
                if header.lower() not in ["content-encoding", "transfer-encoding"]:
                    self.send_header(header, value)
            self.end_headers()
            self.wfile.write(response.content)

        except Exception as e:
            self.send_error(502, f"Backend error: {str(e)[:100]}")

    def serve_static(self, path):
        """Serve static files"""
        if path == "/":
            file_path = FRONTEND_DIR / "index.html"
        else:
            file_path = FRONTEND_DIR / path.lstrip("/")

        # Direct file
        if file_path.exists() and file_path.is_file():
            self.send_file(file_path)
        # Directory -> index.html
        elif file_path.is_dir() and (file_path / "index.html").exists():
            self.send_file(file_path / "index.html")
        # SPA fallback (for /project/:id etc)
        elif not path.endswith((".js", ".css", ".svg", ".woff2", ".png", ".jpg", ".ico", ".json")):
            self.send_file(FRONTEND_DIR / "index.html")
        else:
            self.send_error(404)

    def send_file(self, file_path):
        """Send static file"""
        try:
            with open(file_path, "rb") as f:
                content = f.read()

            # Content types
            ext_to_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json",
                ".svg": "image/svg+xml",
                ".woff2": "font/woff2",
                ".woff": "font/woff",
                ".ttf": "font/ttf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".ico": "image/x-icon",
            }

            ext = Path(file_path).suffix
            content_type = ext_to_type.get(ext, "application/octet-stream")

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))

            # Cache control
            if ext in [".html"]:
                self.send_header("Cache-Control", "no-cache")
            else:
                self.send_header("Cache-Control", "public, max-age=86400")

            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))


def main():
    print("\n" + "=" * 70)
    print("ProjectHub Frontend Proxy (v2)")
    print("=" * 70)
    print(f"\nFrontend:  http://localhost:{FRONTEND_PORT}/")
    print(f"Backend:   {BACKEND_URL}")
    print(f"Files:     {FRONTEND_DIR}")
    print("\nRouting:")
    print(f"  GET /api/*  -> Backend API")
    print(f"  GET /*      -> Static files (SPA)")
    print("\nPress Ctrl+C to stop\n")

    server = HTTPServer(("0.0.0.0", FRONTEND_PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
