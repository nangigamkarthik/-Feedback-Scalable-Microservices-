from __future__ import annotations

import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from common.auth import require_authenticated_user, require_role
from common.db import connect_database, default_sqlite_url, is_postgres_url
from common.http_utils import read_json, send_json


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8002"))
READ_DATABASE_URL = os.environ.get("READ_DATABASE_URL", default_sqlite_url("read.db"))
IS_POSTGRES = is_postgres_url(READ_DATABASE_URL)
AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://127.0.0.1:8004")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_database() -> None:
    with connect_database(READ_DATABASE_URL) as connection:
        if IS_POSTGRES:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_read (
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
                    updated_at TEXT NOT NULL,
                    last_event_id BIGINT NOT NULL,
                    last_event_type TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id BIGINT PRIMARY KEY,
                    feedback_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                )
                """
            )
            connection.execute("ALTER TABLE feedback_read ADD COLUMN IF NOT EXISTS submitted_by_user_id TEXT")
            connection.execute("ALTER TABLE feedback_read ADD COLUMN IF NOT EXISTS submitted_by_name TEXT")
            connection.execute(
                """
                UPDATE feedback_read
                SET submitted_by_user_id = COALESCE(submitted_by_user_id, 'legacy-user'),
                    submitted_by_name = COALESCE(submitted_by_name, customer_name)
                """
            )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_read (
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
                    updated_at TEXT NOT NULL,
                    last_event_id INTEGER NOT NULL,
                    last_event_type TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id INTEGER PRIMARY KEY,
                    feedback_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                )
                """
            )
            connection.execute("ALTER TABLE feedback_read ADD COLUMN IF NOT EXISTS submitted_by_user_id TEXT")
            connection.execute("ALTER TABLE feedback_read ADD COLUMN IF NOT EXISTS submitted_by_name TEXT")
            connection.execute(
                """
                UPDATE feedback_read
                SET submitted_by_user_id = COALESCE(submitted_by_user_id, 'legacy-user'),
                    submitted_by_name = COALESCE(submitted_by_name, customer_name)
                """
            )
        connection.commit()


def apply_event(payload: dict) -> tuple[int, bool, str]:
    event_id = int(payload["event_id"])
    event_type = payload["event_type"]
    body = payload["payload"]

    with connect_database(READ_DATABASE_URL) as connection:
        if IS_POSTGRES:
            already_done = connection.execute(
                "SELECT event_id FROM processed_events WHERE event_id = %s",
                (event_id,),
            ).fetchone()
        else:
            already_done = connection.execute(
                "SELECT event_id FROM processed_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if already_done:
            return 200, False, "Event already projected"

        if event_type == "FeedbackSubmitted":
            if IS_POSTGRES:
                connection.execute(
                    """
                    INSERT INTO feedback_read (
                        id, customer_name, email, category, message, rating,
                        submitted_by_user_id, submitted_by_name,
                        status, admin_comment, created_at, updated_at,
                        last_event_id, last_event_type
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        customer_name = excluded.customer_name,
                        email = excluded.email,
                        category = excluded.category,
                        message = excluded.message,
                        rating = excluded.rating,
                        submitted_by_user_id = excluded.submitted_by_user_id,
                        submitted_by_name = excluded.submitted_by_name,
                        status = excluded.status,
                        admin_comment = excluded.admin_comment,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        last_event_id = excluded.last_event_id,
                        last_event_type = excluded.last_event_type
                    """,
                    (
                        body["id"],
                        body["customer_name"],
                        body["email"],
                        body["category"],
                        body["message"],
                        body["rating"],
                        body["submitted_by_user_id"],
                        body["submitted_by_name"],
                        body["status"],
                        body.get("admin_comment"),
                        body["created_at"],
                        body["updated_at"],
                        event_id,
                        event_type,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO feedback_read (
                        id, customer_name, email, category, message, rating,
                        submitted_by_user_id, submitted_by_name,
                        status, admin_comment, created_at, updated_at,
                        last_event_id, last_event_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        customer_name = excluded.customer_name,
                        email = excluded.email,
                        category = excluded.category,
                        message = excluded.message,
                        rating = excluded.rating,
                        submitted_by_user_id = excluded.submitted_by_user_id,
                        submitted_by_name = excluded.submitted_by_name,
                        status = excluded.status,
                        admin_comment = excluded.admin_comment,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        last_event_id = excluded.last_event_id,
                        last_event_type = excluded.last_event_type
                    """,
                    (
                        body["id"],
                        body["customer_name"],
                        body["email"],
                        body["category"],
                        body["message"],
                        body["rating"],
                        body["submitted_by_user_id"],
                        body["submitted_by_name"],
                        body["status"],
                        body.get("admin_comment"),
                        body["created_at"],
                        body["updated_at"],
                        event_id,
                        event_type,
                    ),
                )
            feedback_id = body["id"]
        elif event_type == "FeedbackStatusChanged":
            if IS_POSTGRES:
                updated = connection.execute(
                    """
                    UPDATE feedback_read
                    SET status = %s,
                        admin_comment = %s,
                        updated_at = %s,
                        last_event_id = %s,
                        last_event_type = %s
                    WHERE id = %s
                    """,
                    (
                        body["status"],
                        body.get("admin_comment"),
                        body["updated_at"],
                        event_id,
                        event_type,
                        body["id"],
                    ),
                )
            else:
                updated = connection.execute(
                    """
                    UPDATE feedback_read
                    SET status = ?,
                        admin_comment = ?,
                        updated_at = ?,
                        last_event_id = ?,
                        last_event_type = ?
                    WHERE id = ?
                    """,
                    (
                        body["status"],
                        body.get("admin_comment"),
                        body["updated_at"],
                        event_id,
                        event_type,
                        body["id"],
                    ),
                )
            if updated.rowcount == 0:
                return 409, False, "Feedback must exist before status updates can be projected"
            feedback_id = body["id"]
        else:
            return 400, False, f"Unsupported event type: {event_type}"

        if IS_POSTGRES:
            connection.execute(
                """
                INSERT INTO processed_events (event_id, feedback_id, event_type, processed_at)
                VALUES (%s, %s, %s, %s)
                """,
                (event_id, feedback_id, event_type, utc_now()),
            )
        else:
            connection.execute(
                """
                INSERT INTO processed_events (event_id, feedback_id, event_type, processed_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, feedback_id, event_type, utc_now()),
            )
        connection.commit()

    return 200, True, "Event projected"


class QueryHandler(BaseHTTPRequestHandler):
    server_version = "FeedbackQueryService/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "query-service",
                    "status": "ok",
                    "auth_service_url": AUTH_SERVICE_URL,
                    "database_engine": "postgresql" if IS_POSTGRES else "sqlite",
                },
            )
            return

        if parsed.path == "/feedback/mine":
            self.handle_my_feedback()
            return

        if parsed.path == "/feedback":
            self.handle_list_feedback(parsed.query)
            return

        if parsed.path.startswith("/feedback/"):
            feedback_id = parsed.path.removeprefix("/feedback/")
            self.handle_get_feedback(feedback_id)
            return

        if parsed.path == "/stats":
            self.handle_stats()
            return

        send_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/internal/events":
            send_json(self, 404, {"error": "Not found"})
            return

        payload = read_json(self)
        required = {"event_id", "event_type", "payload"}
        if not required.issubset(payload):
            send_json(self, 400, {"error": "event_id, event_type, and payload are required"})
            return

        status, projected, message = apply_event(payload)
        send_json(self, status, {"projected": projected, "message": message})

    def handle_list_feedback(self, query_string: str) -> None:
        user = require_role(self, AUTH_SERVICE_URL, {"ADMIN"})
        if not user:
            return

        params = parse_qs(query_string)
        status_filter = params.get("status", [None])[0]
        category_filter = params.get("category", [None])[0]
        search = params.get("search", [None])[0]

        sql = "SELECT * FROM feedback_read WHERE 1=1"
        values: list[str] = []
        placeholder = "%s" if IS_POSTGRES else "?"

        if status_filter:
            sql += f" AND status = {placeholder}"
            values.append(status_filter)

        if category_filter:
            sql += f" AND category = {placeholder}"
            values.append(category_filter)

        if search:
            sql += (
                f" AND (customer_name LIKE {placeholder} OR email LIKE {placeholder} "
                f"OR message LIKE {placeholder})"
            )
            wildcard = f"%{search}%"
            values.extend([wildcard, wildcard, wildcard])

        sql += " ORDER BY created_at DESC"

        with connect_database(READ_DATABASE_URL) as connection:
            rows = connection.execute(sql, values).fetchall()

        feedback = [dict(row) for row in rows]
        send_json(self, 200, {"items": feedback, "count": len(feedback)})

    def handle_my_feedback(self) -> None:
        user = require_authenticated_user(self, AUTH_SERVICE_URL)
        if not user:
            return

        placeholder = "%s" if IS_POSTGRES else "?"
        with connect_database(READ_DATABASE_URL) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM feedback_read
                WHERE submitted_by_user_id = {placeholder}
                ORDER BY created_at DESC
                """,
                (user["id"],),
            ).fetchall()

        feedback = [dict(row) for row in rows]
        send_json(self, 200, {"items": feedback, "count": len(feedback)})

    def handle_get_feedback(self, feedback_id: str) -> None:
        user = require_authenticated_user(self, AUTH_SERVICE_URL)
        if not user:
            return

        with connect_database(READ_DATABASE_URL) as connection:
            if IS_POSTGRES:
                row = connection.execute(
                    "SELECT * FROM feedback_read WHERE id = %s",
                    (feedback_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM feedback_read WHERE id = ?",
                    (feedback_id,),
                ).fetchone()

        if not row:
            send_json(self, 404, {"error": "Feedback not found"})
            return

        if user["role"] != "ADMIN" and row["submitted_by_user_id"] != user["id"]:
            send_json(self, 403, {"error": "You do not have access to this feedback"})
            return

        send_json(self, 200, {"feedback": dict(row)})

    def handle_stats(self) -> None:
        user = require_role(self, AUTH_SERVICE_URL, {"ADMIN"})
        if not user:
            return

        with connect_database(READ_DATABASE_URL) as connection:
            total = connection.execute("SELECT COUNT(*) AS count FROM feedback_read").fetchone()["count"]
            average_rating = connection.execute(
                "SELECT ROUND(COALESCE(AVG(rating), 0), 2) AS average_rating FROM feedback_read"
            ).fetchone()["average_rating"]
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM feedback_read GROUP BY status"
            ).fetchall()

        counts_by_status = {row["status"]: row["count"] for row in rows}
        send_json(
            self,
            200,
            {
                "total_feedback": total,
                "average_rating": float(average_rating),
                "counts_by_status": counts_by_status,
            },
        )

    def log_message(self, format: str, *args) -> None:
        print(f"[query-service] {self.address_string()} - {format % args}")


def main() -> None:
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), QueryHandler)
    print(f"[query-service] listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
