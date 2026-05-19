from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common.auth import require_authenticated_user, require_role
from common.db import connect_database, default_sqlite_url, is_postgres_url
from common.http_utils import proxy_json_request, read_json, send_json
from common.rabbitmq_http import publish_message


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8001"))
WRITE_DATABASE_URL = os.environ.get("WRITE_DATABASE_URL", default_sqlite_url("write.db"))
IS_POSTGRES = is_postgres_url(WRITE_DATABASE_URL)
AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://127.0.0.1:8004")
EVENT_TRANSPORT = os.environ.get("EVENT_TRANSPORT", "http").lower()
PROJECTOR_URL = os.environ.get("PROJECTOR_URL", "http://127.0.0.1:8003")
RABBITMQ_API_URL = os.environ.get("RABBITMQ_API_URL", "http://127.0.0.1:15672/api")
RABBITMQ_QUEUE = os.environ.get("RABBITMQ_QUEUE", "feedback.events")
RABBITMQ_VHOST = os.environ.get("RABBITMQ_VHOST", "/")
RABBITMQ_USERNAME = os.environ.get("RABBITMQ_USERNAME", "guest")
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "guest")
OUTBOX_RETRY_INTERVAL = int(os.environ.get("OUTBOX_RETRY_INTERVAL", "3"))
VALID_STATUSES = {"NEW", "IN_REVIEW", "RESOLVED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_database() -> None:
    with connect_database(WRITE_DATABASE_URL) as connection:
        if IS_POSTGRES:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    submitted_by_user_id TEXT NOT NULL,
                    submitted_by_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    admin_comment TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id BIGSERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    dispatched_at TEXT
                )
                """
            )
            connection.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS submitted_by_user_id TEXT")
            connection.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS submitted_by_name TEXT")
            connection.execute(
                """
                UPDATE feedback
                SET submitted_by_user_id = COALESCE(submitted_by_user_id, 'legacy-user'),
                    submitted_by_name = COALESCE(submitted_by_name, customer_name)
                """
            )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    submitted_by_user_id TEXT NOT NULL,
                    submitted_by_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    admin_comment TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    dispatched_at TEXT
                )
                """
            )
            connection.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS submitted_by_user_id TEXT")
            connection.execute("ALTER TABLE feedback ADD COLUMN IF NOT EXISTS submitted_by_name TEXT")
            connection.execute(
                """
                UPDATE feedback
                SET submitted_by_user_id = COALESCE(submitted_by_user_id, 'legacy-user'),
                    submitted_by_name = COALESCE(submitted_by_name, customer_name)
                """
            )
        connection.commit()


def validate_feedback(payload: dict) -> tuple[bool, str | None]:
    required_fields = ("category", "message", "rating")
    for field in required_fields:
        value = str(payload.get(field, "")).strip()
        if not value:
            return False, f"{field} is required"

    try:
        rating = int(payload["rating"])
    except (TypeError, ValueError):
        return False, "rating must be an integer"

    if rating < 1 or rating > 5:
        return False, "rating must be between 1 and 5"

    return True, None


def publish_event(connection, event_type: str, payload: dict) -> int:
    if IS_POSTGRES:
        cursor = connection.execute(
            """
            INSERT INTO outbox (event_type, payload, created_at)
            VALUES (%s, %s, %s)
            RETURNING event_id
            """,
            (event_type, json.dumps(payload), utc_now()),
        )
        return int(cursor.fetchone()["event_id"])

    cursor = connection.execute(
        """
        INSERT INTO outbox (event_type, payload, created_at)
        VALUES (?, ?, ?)
        """,
        (event_type, json.dumps(payload), utc_now()),
    )
    return int(cursor.lastrowid)


def dispatch_event(event_row) -> bool:
    envelope = {
        "event_id": event_row["event_id"],
        "event_type": event_row["event_type"],
        "occurred_at": event_row["created_at"],
        "payload": json.loads(event_row["payload"]),
    }

    if EVENT_TRANSPORT == "rabbitmq":
        return publish_message(
            RABBITMQ_API_URL,
            RABBITMQ_QUEUE,
            envelope,
            RABBITMQ_USERNAME,
            RABBITMQ_PASSWORD,
            vhost=RABBITMQ_VHOST,
        )

    status, _ = proxy_json_request(
        "POST",
        f"{PROJECTOR_URL}/events",
        payload=envelope,
    )
    return 200 <= status < 300


def try_dispatch_outbox(event_id: int) -> None:
    with connect_database(WRITE_DATABASE_URL) as connection:
        if IS_POSTGRES:
            row = connection.execute(
                "SELECT event_id, event_type, payload, created_at FROM outbox WHERE event_id = %s",
                (event_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT event_id, event_type, payload, created_at FROM outbox WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if not row:
            return

        if dispatch_event(row):
            if IS_POSTGRES:
                connection.execute(
                    "UPDATE outbox SET dispatched_at = %s WHERE event_id = %s",
                    (utc_now(), event_id),
                )
            else:
                connection.execute(
                    "UPDATE outbox SET dispatched_at = ? WHERE event_id = ?",
                    (utc_now(), event_id),
                )
            connection.commit()


def outbox_worker() -> None:
    while True:
        try:
            with connect_database(WRITE_DATABASE_URL) as connection:
                if IS_POSTGRES:
                    rows = connection.execute(
                        """
                        SELECT event_id, event_type, payload, created_at
                        FROM outbox
                        WHERE dispatched_at IS NULL
                        ORDER BY event_id ASC
                        LIMIT 20
                        """
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT event_id, event_type, payload, created_at
                        FROM outbox
                        WHERE dispatched_at IS NULL
                        ORDER BY event_id ASC
                        LIMIT 20
                        """
                    ).fetchall()

                for row in rows:
                    if dispatch_event(row):
                        if IS_POSTGRES:
                            connection.execute(
                                "UPDATE outbox SET dispatched_at = %s WHERE event_id = %s",
                                (utc_now(), row["event_id"]),
                            )
                        else:
                            connection.execute(
                                "UPDATE outbox SET dispatched_at = ? WHERE event_id = ?",
                                (utc_now(), row["event_id"]),
                            )
                connection.commit()
        except Exception as error:  # pragma: no cover - dev visibility
            print(f"[command-service] outbox retry failed: {error}")

        time.sleep(OUTBOX_RETRY_INTERVAL)


class CommandHandler(BaseHTTPRequestHandler):
    server_version = "FeedbackCommandService/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "command-service",
                    "status": "ok",
                    "database_engine": "postgresql" if IS_POSTGRES else "sqlite",
                    "auth_service_url": AUTH_SERVICE_URL,
                    "event_transport": EVENT_TRANSPORT,
                    "projector_url": PROJECTOR_URL,
                    "rabbitmq_queue": RABBITMQ_QUEUE,
                },
            )
            return

        send_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/feedback":
            send_json(self, 404, {"error": "Not found"})
            return

        user = require_authenticated_user(self, AUTH_SERVICE_URL)
        if not user:
            return

        payload = read_json(self)
        is_valid, error = validate_feedback(payload)
        if not is_valid:
            send_json(self, 400, {"error": error})
            return

        now = utc_now()
        feedback = {
            "id": str(uuid.uuid4()),
            "customer_name": user["full_name"],
            "email": user["email"],
            "category": payload["category"].strip(),
            "message": payload["message"].strip(),
            "rating": int(payload["rating"]),
            "submitted_by_user_id": user["id"],
            "submitted_by_name": user["full_name"],
            "status": "NEW",
            "admin_comment": None,
            "created_at": now,
            "updated_at": now,
        }

        with connect_database(WRITE_DATABASE_URL) as connection:
            if IS_POSTGRES:
                connection.execute(
                    """
                    INSERT INTO feedback (
                        id, customer_name, email, category, message, rating,
                        submitted_by_user_id, submitted_by_name,
                        status, admin_comment, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        feedback["id"],
                        feedback["customer_name"],
                        feedback["email"],
                        feedback["category"],
                        feedback["message"],
                        feedback["rating"],
                        feedback["submitted_by_user_id"],
                        feedback["submitted_by_name"],
                        feedback["status"],
                        feedback["admin_comment"],
                        feedback["created_at"],
                        feedback["updated_at"],
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO feedback (
                        id, customer_name, email, category, message, rating,
                        submitted_by_user_id, submitted_by_name,
                        status, admin_comment, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        feedback["id"],
                        feedback["customer_name"],
                        feedback["email"],
                        feedback["category"],
                        feedback["message"],
                        feedback["rating"],
                        feedback["submitted_by_user_id"],
                        feedback["submitted_by_name"],
                        feedback["status"],
                        feedback["admin_comment"],
                        feedback["created_at"],
                        feedback["updated_at"],
                    ),
                )
            event_id = publish_event(connection, "FeedbackSubmitted", feedback)
            connection.commit()

        try_dispatch_outbox(event_id)
        send_json(self, 201, {"message": "Feedback submitted", "feedback": feedback})

    def do_PATCH(self) -> None:
        if not self.path.startswith("/feedback/") or not self.path.endswith("/status"):
            send_json(self, 404, {"error": "Not found"})
            return

        user = require_role(self, AUTH_SERVICE_URL, {"ADMIN"})
        if not user:
            return

        feedback_id = self.path.removeprefix("/feedback/").removesuffix("/status").strip("/")
        payload = read_json(self)
        new_status = str(payload.get("status", "")).strip().upper()
        admin_comment = payload.get("admin_comment")

        if new_status not in VALID_STATUSES:
            send_json(self, 400, {"error": f"status must be one of {sorted(VALID_STATUSES)}"})
            return

        with connect_database(WRITE_DATABASE_URL) as connection:
            if IS_POSTGRES:
                existing = connection.execute(
                    "SELECT id FROM feedback WHERE id = %s",
                    (feedback_id,),
                ).fetchone()
            else:
                existing = connection.execute(
                    "SELECT id FROM feedback WHERE id = ?",
                    (feedback_id,),
                ).fetchone()
            if not existing:
                send_json(self, 404, {"error": "Feedback not found"})
                return

            updated_at = utc_now()
            if IS_POSTGRES:
                connection.execute(
                    """
                    UPDATE feedback
                    SET status = %s, admin_comment = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (new_status, admin_comment, updated_at, feedback_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE feedback
                    SET status = ?, admin_comment = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_status, admin_comment, updated_at, feedback_id),
                )

            event_id = publish_event(
                connection,
                "FeedbackStatusChanged",
                {
                    "id": feedback_id,
                    "status": new_status,
                    "admin_comment": admin_comment,
                    "updated_at": updated_at,
                },
            )
            connection.commit()

        try_dispatch_outbox(event_id)
        send_json(
            self,
            200,
            {
                "message": "Feedback status updated",
                "feedback_id": feedback_id,
                "status": new_status,
                "admin_comment": admin_comment,
            },
        )

    def log_message(self, format: str, *args) -> None:
        print(f"[command-service] {self.address_string()} - {format % args}")


def main() -> None:
    initialize_database()
    worker = threading.Thread(target=outbox_worker, daemon=True)
    worker.start()

    server = ThreadingHTTPServer((HOST, PORT), CommandHandler)
    print(f"[command-service] listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
