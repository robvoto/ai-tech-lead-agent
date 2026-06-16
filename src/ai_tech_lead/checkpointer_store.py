"""Persistent SQLite checkpointer singleton for LangGraph workflows.

All graphs share this checkpointer. Thread isolation is via thread_id in the
configurable — each task/chat/session gets a unique thread_id, so separate
conversations never mix. Threads persist across process restarts.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .config import DATA_DIR, ensure_project_dirs

if TYPE_CHECKING:
    from langgraph.checkpoint.sqlite import SqliteSaver

_CHECKPOINT_DB = DATA_DIR / "checkpoints.sqlite3"
_conn: sqlite3.Connection | None = None
_checkpointer: "SqliteSaver | None" = None


def get_checkpointer() -> "SqliteSaver":
    """Return the module-level SqliteSaver singleton, creating it on first call."""
    global _conn, _checkpointer
    if _checkpointer is None:
        from langgraph.checkpoint.sqlite import SqliteSaver

        ensure_project_dirs()
        _conn = sqlite3.connect(str(_CHECKPOINT_DB), check_same_thread=False)
        _checkpointer = SqliteSaver(_conn)
    return _checkpointer
