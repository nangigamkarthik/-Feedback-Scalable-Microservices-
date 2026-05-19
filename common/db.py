from __future__ import annotations

import sqlite3
from pathlib import Path


def default_sqlite_url(filename: str) -> str:
    stem = Path(filename).stem.replace(" ", "-")
    return f"file:{stem}?mode=memory&cache=shared"


def is_postgres_url(database_url: str) -> bool:
    return database_url.startswith("postgresql://") or database_url.startswith("postgres://")


def connect_database(database_url: str):
    if is_postgres_url(database_url):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(database_url, autocommit=False, row_factory=dict_row)

    connection = sqlite3.connect(
        database_url,
        check_same_thread=False,
        uri=database_url.startswith("file:"),
    )
    connection.row_factory = sqlite3.Row
    return connection
