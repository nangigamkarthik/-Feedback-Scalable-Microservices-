from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common.http_utils import proxy_json_request, read_json, send_json
from common.rabbitmq_http import declare_queue, get_messages, publish_message


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8003"))
QUERY_SERVICE_URL = os.environ.get("QUERY_SERVICE_URL", "http://127.0.0.1:8002")
EVENT_TRANSPORT = os.environ.get("EVENT_TRANSPORT", "http").lower()
RABBITMQ_API_URL = os.environ.get("RABBITMQ_API_URL", "http://127.0.0.1:15672/api")
RABBITMQ_QUEUE = os.environ.get("RABBITMQ_QUEUE", "feedback.events")
RABBITMQ_VHOST = os.environ.get("RABBITMQ_VHOST", "/")
RABBITMQ_USERNAME = os.environ.get("RABBITMQ_USERNAME", "guest")
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "guest")
RABBITMQ_POLL_INTERVAL = float(os.environ.get("RABBITMQ_POLL_INTERVAL", "1.5"))


def project_event(payload: dict) -> tuple[int, bytes]:
    return proxy_json_request(
        "POST",
        f"{QUERY_SERVICE_URL}/internal/events",
        payload=payload,
    )


def rabbitmq_consumer() -> None:
    queue_ready = False
    while True:
        try:
            if not queue_ready:
                queue_ready = declare_queue(
                    RABBITMQ_API_URL,
                    RABBITMQ_QUEUE,
                    RABBITMQ_USERNAME,
                    RABBITMQ_PASSWORD,
                    vhost=RABBITMQ_VHOST,
                )
                if not queue_ready:
                    time.sleep(RABBITMQ_POLL_INTERVAL)
                    continue

            messages = get_messages(
                RABBITMQ_API_URL,
                RABBITMQ_QUEUE,
                RABBITMQ_USERNAME,
                RABBITMQ_PASSWORD,
                count=10,
                vhost=RABBITMQ_VHOST,
            )
            if not messages:
                time.sleep(RABBITMQ_POLL_INTERVAL)
                continue

            for message in messages:
                status, body = project_event(message)
                if status >= 300:
                    print(
                        "[projector] projection failed from rabbitmq:",
                        status,
                        body.decode("utf-8"),
                    )
                    if status >= 500 or status == 409:
                        publish_message(
                            RABBITMQ_API_URL,
                            RABBITMQ_QUEUE,
                            message,
                            RABBITMQ_USERNAME,
                            RABBITMQ_PASSWORD,
                            vhost=RABBITMQ_VHOST,
                        )
        except Exception as error:  # pragma: no cover - dev visibility
            print(f"[projector] rabbitmq consume failed: {error}")

        time.sleep(RABBITMQ_POLL_INTERVAL)


class ProjectorHandler(BaseHTTPRequestHandler):
    server_version = "FeedbackProjector/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "projector",
                    "status": "ok",
                    "event_transport": EVENT_TRANSPORT,
                    "query_service_url": QUERY_SERVICE_URL,
                    "rabbitmq_queue": RABBITMQ_QUEUE,
                },
            )
            return

        send_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/events":
            send_json(self, 404, {"error": "Not found"})
            return

        payload = read_json(self)
        if not payload.get("event_type") or payload.get("event_id") is None:
            send_json(self, 400, {"error": "event_id and event_type are required"})
            return

        status, body = project_event(payload)
        if status >= 300:
            send_json(
                self,
                status,
                {
                    "error": "Projection failed",
                    "downstream_response": body.decode("utf-8"),
                },
            )
            return

        send_json(
            self,
            202,
            {"message": "Event accepted for projection", "event_id": payload["event_id"]},
        )

    def log_message(self, format: str, *args) -> None:
        print(f"[projector] {self.address_string()} - {format % args}")


def main() -> None:
    if EVENT_TRANSPORT == "rabbitmq":
        worker = threading.Thread(target=rabbitmq_consumer, daemon=True)
        worker.start()

    server = ThreadingHTTPServer((HOST, PORT), ProjectorHandler)
    print(f"[projector] listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
