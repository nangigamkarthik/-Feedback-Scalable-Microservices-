from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common.db import connect_database, default_sqlite_url, is_postgres_url
from common.http_utils import read_json, send_json


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8004"))
AUTH_DATABASE_URL = os.environ.get("AUTH_DATABASE_URL", default_sqlite_url("auth.db"))
IS_POSTGRES = is_postgres_url(AUTH_DATABASE_URL)
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "12"))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@feedback.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Admin User")
USER_EMAIL = os.environ.get("USER_EMAIL", "user@feedback.local")
USER_PASSWORD = os.environ.get("USER_PASSWORD", "user123")
USER_NAME = os.environ.get("USER_NAME", "Feedback User")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(email: str, password: str) -> str:
    return hashlib.sha256(f"{email.lower()}::{password}".encode("utf-8")).hexdigest()


def initialize_database() -> None:
    with connect_database(AUTH_DATABASE_URL) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        connection.commit()

    seed_user(ADMIN_NAME, ADMIN_EMAIL, ADMIN_PASSWORD, "ADMIN")
    seed_user(USER_NAME, USER_EMAIL, USER_PASSWORD, "USER")


def seed_user(full_name: str, email: str, password: str, role: str) -> None:
    with connect_database(AUTH_DATABASE_URL) as connection:
        row = fetchone(
            connection,
            "SELECT id FROM users WHERE email = {p}",
            (email.lower(),),
        )
        if row:
            execute(
                connection,
                """
                UPDATE users
                SET full_name = {p}, password_hash = {p}, role = {p}
                WHERE email = {p}
                """,
                (full_name, hash_password(email, password), role, email.lower()),
            )
        else:
            execute(
                connection,
                """
                INSERT INTO users (id, full_name, email, password_hash, role, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
                """,
                (
                    str(uuid.uuid4()),
                    full_name,
                    email.lower(),
                    hash_password(email, password),
                    role,
                    utc_now(),
                ),
            )
        connection.commit()


def placeholder_sql(sql: str) -> str:
    return sql.replace("{p}", "%s" if IS_POSTGRES else "?")


def execute(connection, sql: str, params: tuple = ()):
    return connection.execute(placeholder_sql(sql), params)


def fetchone(connection, sql: str, params: tuple = ()):
    return execute(connection, sql, params).fetchone()


def fetch_user_by_email(email: str):
    with connect_database(AUTH_DATABASE_URL) as connection:
        return fetchone(
            connection,
            "SELECT id, full_name, email, password_hash, role FROM users WHERE email = {p}",
            (email.lower(),),
        )


def create_session(user_id: str) -> str:
    token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    with connect_database(AUTH_DATABASE_URL) as connection:
        execute(
            connection,
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES ({p}, {p}, {p}, {p})",
            (token, user_id, utc_now(), expires_at),
        )
        connection.commit()
    return token


def get_authenticated_user(token: str):
    with connect_database(AUTH_DATABASE_URL) as connection:
        execute(
            connection,
            "DELETE FROM sessions WHERE expires_at < {p}",
            (utc_now(),),
        )
        row = fetchone(
            connection,
            """
            SELECT users.id, users.full_name, users.email, users.role
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = {p} AND sessions.expires_at >= {p}
            """,
            (token, utc_now()),
        )
        connection.commit()
    return row


class AuthHandler(BaseHTTPRequestHandler):
    server_version = "FeedbackAuthService/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "auth-service",
                    "status": "ok",
                    "database_engine": "postgresql" if IS_POSTGRES else "sqlite",
                },
            )
            return

        if self.path == "/me":
            self.handle_me()
            return

        send_json(self, 404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path == "/login":
            self.handle_login()
            return

        send_json(self, 404, {"error": "Not found"})

    def handle_login(self) -> None:
        payload = read_json(self)
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))

        if not email or not password:
            send_json(self, 400, {"error": "email and password are required"})
            return

        user = fetch_user_by_email(email)
        if not user or user["password_hash"] != hash_password(email, password):
            send_json(self, 401, {"error": "Invalid credentials"})
            return

        token = create_session(user["id"])
        send_json(
            self,
            200,
            {
                "token": token,
                "user": {
                    "id": user["id"],
                    "full_name": user["full_name"],
                    "email": user["email"],
                    "role": user["role"],
                },
            },
        )

    def handle_me(self) -> None:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            send_json(self, 401, {"error": "Missing bearer token"})
            return

        token = authorization.removeprefix("Bearer ").strip()
        user = get_authenticated_user(token)
        if not user:
            send_json(self, 401, {"error": "Invalid or expired token"})
            return

        send_json(
            self,
            200,
            {
                "user": {
                    "id": user["id"],
                    "full_name": user["full_name"],
                    "email": user["email"],
                    "role": user["role"],
                }
            },
        )

    def log_message(self, format: str, *args) -> None:
        print(f"[auth-service] {self.address_string()} - {format % args}")


def main() -> None:
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), AuthHandler)
    print(f"[auth-service] listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
