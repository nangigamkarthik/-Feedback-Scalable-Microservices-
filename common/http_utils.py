from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def read_json(handler: BaseHTTPRequestHandler) -> dict:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length == 0:
        return {}

    raw_body = handler.rfile.read(content_length)
    if not raw_body:
        return {}

    return json.loads(raw_body.decode("utf-8"))


def send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict | list) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_html(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def proxy_json_request(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 5,
) -> tuple[int, bytes]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = Request(url, data=data, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except URLError as error:
        message = json.dumps({"error": str(error.reason)}).encode("utf-8")
        return 503, message
