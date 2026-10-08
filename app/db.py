"""SQLite persistence for published reader documents."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    """Create the local documents table if it does not exist yet."""
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_url TEXT NOT NULL,
                title TEXT NOT NULL,
                content_md TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def insert_document(document: dict) -> None:
    """Insert a fully validated document record."""
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO documents
                (id, source_type, source_url, title, content_md, created_at)
            VALUES
                (:id, :source_type, :source_url, :title, :content_md, :created_at)
            """,
            document,
        )


def get_document(slug: str) -> dict | None:
    """Return one document row as a dictionary, if it exists."""
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (slug,)
        ).fetchone()
    return dict(row) if row is not None else None


def delete_document(slug: str) -> bool:
    """Delete one document; return whether a row was removed."""
    with _connection() as connection:
        cursor = connection.execute("DELETE FROM documents WHERE id = ?", (slug,))
        return cursor.rowcount > 0
