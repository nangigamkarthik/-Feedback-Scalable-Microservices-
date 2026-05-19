from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common.http_utils import send_html, send_json


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
AUTH_BASE_URL = os.environ.get("AUTH_BASE_URL", "http://127.0.0.1:8004")
COMMAND_BASE_URL = os.environ.get("COMMAND_BASE_URL", "http://127.0.0.1:8001")
QUERY_BASE_URL = os.environ.get("QUERY_BASE_URL", "http://127.0.0.1:8002")
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "frontend"))


def read_frontend() -> str:
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


def read_static_asset(asset_name: str) -> tuple[bytes, str] | None:
    path = FRONTEND_DIR / asset_name.lstrip("/")
    if not path.exists() or not path.is_file():
        return None

    content_type = "text/plain; charset=utf-8"
    if path.suffix == ".js":
        content_type = "application/javascript; charset=utf-8"
    elif path.suffix == ".css":
        content_type = "text/css; charset=utf-8"
    elif path.suffix == ".html":
        content_type = "text/html; charset=utf-8"

    return path.read_bytes(), content_type


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "FeedbackGateway/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "gateway",
                    "status": "ok",
                    "auth_base_url": AUTH_BASE_URL,
                    "command_base_url": COMMAND_BASE_URL,
                    "query_base_url": QUERY_BASE_URL,
                },
            )
            return

        if self.path in {"/", "/index.html"}:
            send_html(self, read_frontend())
            return

        asset = read_static_asset(self.path)
        if asset:
            body, content_type = asset
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path.startswith("/api/auth/"):
            self.proxy_request(AUTH_BASE_URL, "/api/auth/")
            return

        if self.path.startswith("/api/queries/"):
            self.proxy_request(QUERY_BASE_URL, "/api/queries/")
            return

        send_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path.startswith("/api/auth/"):
            self.proxy_request(AUTH_BASE_URL, "/api/auth/")
            return

        if self.path.startswith("/api/commands/"):
            self.proxy_request(COMMAND_BASE_URL, "/api/commands/")
            return

        send_json(self, 404, {"error": "Not found"})

    def do_PATCH(self) -> None:
        if self.path.startswith("/api/commands/"):
            self.proxy_request(COMMAND_BASE_URL, "/api/commands/")
            return

        send_json(self, 404, {"error": "Not found"})

    def proxy_request(self, base_url: str, prefix: str) -> None:
        upstream_path = self.path.removeprefix(prefix)
        upstream_url = f"{base_url}/{upstream_path}"

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None

        headers = {}
        if self.headers.get("Content-Type"):
            headers["Content-Type"] = self.headers["Content-Type"]
        if self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]

        request = Request(upstream_url, data=body, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=5) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except URLError as error:
            send_json(self, 503, {"error": f"Upstream unavailable: {error.reason}"})

    def log_message(self, format: str, *args) -> None:
        print(f"[gateway] {self.address_string()} - {format % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    print(f"[gateway] listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
