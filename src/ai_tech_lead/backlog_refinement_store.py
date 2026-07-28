"""Durable runtime state for pending Hub backlog refinement approvals."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from .backlog_refinement_capability import BacklogRefinementProposal
from .config import DATA_DIR, ensure_project_dirs

BACKLOG_REFINEMENT_DB_PATH = DATA_DIR / "backlog_refinement.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_backlog_refinements (
    request_id TEXT PRIMARY KEY,
    proposal_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class PendingBacklogRefinement:
    request_id: str
    proposal: BacklogRefinementProposal
    created_at: str
    updated_at: str


@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BacklogRefinementStore:
    """Small durable store for pending Hub backlog refinement approvals."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or BACKLOG_REFINEMENT_DB_PATH
        ensure_project_dirs()

    def save_pending(self, *, request_id: str, proposal: BacklogRefinementProposal) -> None:
        now = _now_iso()
        with _connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO pending_backlog_refinements
                   (request_id, proposal_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(request_id) DO UPDATE SET
                     proposal_json = excluded.proposal_json,
                     updated_at = excluded.updated_at""",
                (
                    request_id,
                    json.dumps(proposal.to_payload(), ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def get_pending(self, request_id: str) -> PendingBacklogRefinement | None:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT request_id, proposal_json, created_at, updated_at
                   FROM pending_backlog_refinements WHERE request_id = ?""",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return PendingBacklogRefinement(
            request_id=row["request_id"],
            proposal=BacklogRefinementProposal.from_payload(json.loads(row["proposal_json"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def clear_pending(self, request_id: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM pending_backlog_refinements WHERE request_id = ?",
                (request_id,),
            )
