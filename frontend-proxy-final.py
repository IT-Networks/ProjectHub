#!/usr/bin/env python3
"""ProjectHub Frontend Proxy - stdlib only"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import urllib.request
import urllib.error
import sys

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
BACKEND_URL = "http://localhost:3001"
PORT = 3000


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Minimal logging
        pass

    def handle_one_request(self):
        """Override to handle both API and static"""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ''
                self.request_version = ''
                self.command = ''
                self.send_error(414)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return

            # Route to handler
            if self.path.startswith("/api/"):
                self.handle_api()
            else:
                self.handle_static()

        except Exception as e:
            print(f"Error: {e}")

    def handle_api(self):
        """Proxy to backend"""
        try:
            url = f"{BACKEND_URL}{self.path}"

            # Read body if POST/PUT
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            # Build headers
            headers = {}
            for header, value in self.headers.items():
                if header.lower() not in ['host', 'connection', 'content-length']:
                    headers[header] = value

            # Make request
            req = urllib.request.Request(
                url,
                data=body,
                method=self.command,
                headers=headers
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                status = response.status
                resp_body = response.read()

                self.send_response(status)
                for header, value in response.headers.items():
                    if header.lower() not in ['content-encoding', 'transfer-encoding', 'connection']:
                        self.send_header(header, value)
                self.send_header('Content-Length', len(resp_body))
                self.end_headers()
                self.wfile.write(resp_body)
                print(f"[{status}] {self.command} /api/... -> {url[:50]}")

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            body = b'{"error": "Backend error"}'
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
            print(f"[{e.code}] {self.command} /api/...")
        except Exception as e:
            self.send_error(502, f"Backend: {str(e)[:50]}")
            print(f"[502] {self.command} /api/... - {str(e)[:50]}")

    def handle_static(self):
        """Serve static files"""
        path = self.path.split('?')[0]
        if path == '/':
            file_path = FRONTEND_DIR / 'index.html'
        else:
            file_path = FRONTEND_DIR / path.lstrip('/')

        # Direct file
        if file_path.exists() and file_path.is_file():
            self.send_file(file_path)
        # SPA fallback
        elif not any(path.endswith(ext) for ext in ['.js', '.css', '.svg', '.woff2', '.png', '.jpg', '.ico']):
            self.send_file(FRONTEND_DIR / 'index.html')
        else:
            self.send_error(404)

    def send_file(self, file_path):
        """Send file"""
        try:
            data = file_path.read_bytes()
            ext_map = {
                '.html': 'text/html; charset=utf-8',
                '.css': 'text/css',
                '.js': 'application/javascript',
                '.json': 'application/json',
                '.svg': 'image/svg+xml',
                '.woff2': 'font/woff2',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.ico': 'image/x-icon',
            }
            ct = ext_map.get(file_path.suffix, 'application/octet-stream')

            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', len(data))
            cache = 'no-cache' if file_path.suffix == '.html' else 'max-age=86400'
            self.send_header('Cache-Control', cache)
            self.end_headers()
            self.wfile.write(data)
            print(f"[200] GET {self.path[:50]} ({len(data)} bytes)")
        except Exception as e:
            self.send_error(500)
            print(f"[500] GET {self.path} - {e}")


def main():
    print("\n" + "="*70)
    print("ProjectHub Frontend Proxy")
    print("="*70)
    print(f"Frontend: http://localhost:{PORT}/")
    print(f"Backend:  {BACKEND_URL}")
    print(f"Files:    {FRONTEND_DIR}")
    print("\nStarting...\n")

    server = HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutdown")
        server.server_close()


if __name__ == "__main__":
    main()
