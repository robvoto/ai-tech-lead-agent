import sqlite3
from pathlib import Path

from ai_tech_lead.config import DB_PATH, ensure_project_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def initialize_database(db_path: Path = DB_PATH) -> Path:
    ensure_project_dirs()
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO app_events (event_type, message) VALUES (?, ?)",
            ("startup", "SQLite ready"),
        )
        connection.commit()
    return db_path
