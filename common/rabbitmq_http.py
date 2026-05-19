from __future__ import annotations

import base64
import json
from urllib.parse import quote

from common.http_utils import proxy_json_request


def _auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _headers(username: str, password: str) -> dict[str, str]:
    return {"Authorization": _auth_header(username, password)}


def declare_queue(
    api_url: str,
    queue_name: str,
    username: str,
    password: str,
    vhost: str = "/",
) -> bool:
    status, _ = proxy_json_request(
        "PUT",
        f"{api_url}/queues/{quote(vhost, safe='')}/{quote(queue_name, safe='')}",
        payload={"auto_delete": False, "durable": True, "arguments": {}},
        headers=_headers(username, password),
    )
    return 200 <= status < 300


def publish_message(
    api_url: str,
    queue_name: str,
    message: dict,
    username: str,
    password: str,
    vhost: str = "/",
) -> bool:
    status, body = proxy_json_request(
        "POST",
        f"{api_url}/exchanges/{quote(vhost, safe='')}/amq.default/publish",
        payload={
            "properties": {"delivery_mode": 2},
            "routing_key": queue_name,
            "payload": json.dumps(message),
            "payload_encoding": "string",
        },
        headers=_headers(username, password),
    )
    if not 200 <= status < 300:
        return False

    response = json.loads(body.decode("utf-8"))
    return bool(response.get("routed"))


def get_messages(
    api_url: str,
    queue_name: str,
    username: str,
    password: str,
    count: int = 10,
    vhost: str = "/",
) -> list[dict]:
    status, body = proxy_json_request(
        "POST",
        f"{api_url}/queues/{quote(vhost, safe='')}/{quote(queue_name, safe='')}/get",
        payload={
            "count": count,
            "ackmode": "ack_requeue_false",
            "encoding": "auto",
            "truncate": 50000,
        },
        headers=_headers(username, password),
    )
    if not 200 <= status < 300:
        return []

    payload = json.loads(body.decode("utf-8"))
    messages: list[dict] = []
    for item in payload:
        raw_payload = item.get("payload")
        if not raw_payload:
            continue
        try:
            messages.append(json.loads(raw_payload))
        except json.JSONDecodeError:
            continue
    return messages
