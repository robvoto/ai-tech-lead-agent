import sqlite3
from pathlib import Path

from ai_tech_lead.config import DB_PATH, ensure_project_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
SCHEMA_VERSION = 1


def _ensure_schema_version(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
        return
    if int(row[0]) != SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported app database schema version: {row[0]}")


def initialize_database(db_path: Path = DB_PATH) -> Path:
    ensure_project_dirs()
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
        _ensure_schema_version(connection)
        connection.execute(
            "INSERT INTO app_events (event_type, message) VALUES (?, ?)",
            ("startup", "SQLite ready"),
        )
        connection.commit()
    return db_path
