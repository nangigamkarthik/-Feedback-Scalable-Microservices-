from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from common.http_utils import proxy_json_request, send_json


def extract_bearer_token(handler: BaseHTTPRequestHandler) -> str | None:
    header = handler.headers.get("Authorization", "").strip()
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip() or None


def verify_access_token(auth_service_url: str, token: str | None) -> dict | None:
    if not token:
        return None

    status, body = proxy_json_request(
        "GET",
        f"{auth_service_url}/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    if status != 200:
        return None

    payload = json.loads(body.decode("utf-8"))
    return payload.get("user")


def require_authenticated_user(handler: BaseHTTPRequestHandler, auth_service_url: str) -> dict | None:
    token = extract_bearer_token(handler)
    user = verify_access_token(auth_service_url, token)
    if user:
        return user

    send_json(handler, 401, {"error": "Authentication required"})
    return None


def require_role(
    handler: BaseHTTPRequestHandler,
    auth_service_url: str,
    allowed_roles: set[str],
) -> dict | None:
    user = require_authenticated_user(handler, auth_service_url)
    if not user:
        return None

    if user.get("role") in allowed_roles:
        return user

    send_json(handler, 403, {"error": "You do not have permission for this action"})
    return None
