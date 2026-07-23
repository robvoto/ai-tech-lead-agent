"""SQLite runtime state for backlog execution.

Google Sheets is the only canonical backlog (see docs/INDEX.md). SQLite here
holds only durable runtime state needed for task execution, recovery,
conflict detection, and pending Google Sheet updates. It deliberately does
NOT expose backlog create/edit/prioritise/list/planning operations — that
surface lives entirely in backlog_sheets_repository.py.

Write-ahead outbox: a completed task's Sheet update is always enqueued
locally first, then an immediate flush is attempted. If the source row
changed since the snapshot was taken, the update is abandoned and a
conflict is recorded instead of silently overwriting a human edit. If the
Sheets call itself fails, the row stays pending for bounded recovery.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator

from .backlog_sheets_repository import SheetsBacklogRepository, repository_for
from .config import DATA_DIR, ensure_project_dirs
from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

__all__ = [
    "BACKLOG_DB_PATH",
    "BacklogRuntimeStore",
    "PendingBacklogUpdate",
    "TaskSnapshot",
    "enqueue_and_flush_update",
    "recover_pending_backlog_updates",
    "run_pending_backlog_recovery",
]

BACKLOG_DB_PATH = DATA_DIR / "backlog.sqlite3"
_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_snapshots (
    request_id     TEXT PRIMARY KEY,
    project_key    TEXT NOT NULL,
    spreadsheet_id TEXT NOT NULL,
    sheet_name     TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    row_data       TEXT NOT NULL,
    row_hash       TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    run_status     TEXT NOT NULL DEFAULT 'active',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_backlog_updates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT NOT NULL,
    item_id           TEXT NOT NULL,
    spreadsheet_id    TEXT NOT NULL,
    sheet_name        TEXT NOT NULL,
    update_fields     TEXT NOT NULL,
    expected_row_hash TEXT NOT NULL,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    last_error        TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    last_attempted_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_conflicts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id              TEXT NOT NULL,
    item_id                 TEXT NOT NULL,
    spreadsheet_id          TEXT NOT NULL,
    sheet_name              TEXT NOT NULL,
    expected_row_hash       TEXT NOT NULL,
    actual_row_hash         TEXT NOT NULL,
    actual_row_data         TEXT NOT NULL,
    attempted_update_fields TEXT NOT NULL,
    detected_at             TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'open'
);
"""


@dataclass(frozen=True)
class TaskSnapshot:
    request_id: str
    project_key: str
    spreadsheet_id: str
    sheet_name: str
    item_id: str
    row_data: list[str]
    row_hash: str
    fetched_at: str
    run_status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PendingBacklogUpdate:
    id: int
    request_id: str
    item_id: str
    spreadsheet_id: str
    sheet_name: str
    update_fields: dict[str, str]
    expected_row_hash: str
    attempt_count: int
    max_attempts: int
    status: str
    last_error: str
    created_at: str
    last_attempted_at: str | None


@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        _ensure_schema_version(conn)
        conn.commit()
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema_version(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", _SCHEMA_VERSION),
        )
        return

    current_version = int(row["value"])
    if current_version == _SCHEMA_VERSION:
        return
    if current_version == 1:
        logger.warning(
            "Migrating backlog runtime DB from schema v1 (independent SQLite "
            "backlog ownership) to v2 (runtime-state only). Dropping the old "
            "backlog_items table; Google Sheets is the canonical backlog and "
            "that table's data was a stale, unsynced mirror."
        )
        conn.execute("DROP TABLE IF EXISTS backlog_items")
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (_SCHEMA_VERSION,),
        )
        return
    raise RuntimeError(f"Unsupported backlog runtime schema version: {current_version}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_snapshot(row: sqlite3.Row) -> TaskSnapshot:
    return TaskSnapshot(
        request_id=row["request_id"],
        project_key=row["project_key"],
        spreadsheet_id=row["spreadsheet_id"],
        sheet_name=row["sheet_name"],
        item_id=row["item_id"],
        row_data=json.loads(row["row_data"]),
        row_hash=row["row_hash"],
        fetched_at=row["fetched_at"],
        run_status=row["run_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_pending_update(row: sqlite3.Row) -> PendingBacklogUpdate:
    return PendingBacklogUpdate(
        id=row["id"],
        request_id=row["request_id"],
        item_id=row["item_id"],
        spreadsheet_id=row["spreadsheet_id"],
        sheet_name=row["sheet_name"],
        update_fields=json.loads(row["update_fields"]),
        expected_row_hash=row["expected_row_hash"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        status=row["status"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        last_attempted_at=row["last_attempted_at"],
    )


class BacklogRuntimeStore:
    """Runtime-state store: task snapshots, pending Sheet updates, conflicts.

    Deliberately has no list/add/update-status/complete-item methods — that
    is backlog ownership, and this store must never become one.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or BACKLOG_DB_PATH
        ensure_project_dirs()

    def ensure_initialized(self) -> None:
        """Create the schema if needed. Used by workspace bootstrap/health checks."""
        with _connect(self._db_path):
            pass

    def save_snapshot(
        self,
        *,
        request_id: str,
        project_key: str,
        spreadsheet_id: str,
        sheet_name: str,
        item_id: str,
        row_data: list[str],
        row_hash: str,
        fetched_at: str,
    ) -> None:
        now = _now_iso()
        with _connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO task_snapshots
                   (request_id, project_key, spreadsheet_id, sheet_name, item_id,
                    row_data, row_hash, fetched_at, run_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                   ON CONFLICT(request_id) DO UPDATE SET
                     row_data = excluded.row_data,
                     row_hash = excluded.row_hash,
                     fetched_at = excluded.fetched_at,
                     updated_at = excluded.updated_at""",
                (
                    request_id,
                    project_key,
                    spreadsheet_id,
                    sheet_name,
                    item_id,
                    json.dumps(row_data),
                    row_hash,
                    fetched_at,
                    now,
                    now,
                ),
            )

    def get_snapshot(self, request_id: str) -> TaskSnapshot | None:
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM task_snapshots WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return _row_to_snapshot(row) if row is not None else None

    def mark_snapshot_status(self, request_id: str, run_status: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE task_snapshots SET run_status = ?, updated_at = ? WHERE request_id = ?",
                (run_status, _now_iso(), request_id),
            )

    def enqueue_pending_update(
        self,
        *,
        request_id: str,
        item_id: str,
        spreadsheet_id: str,
        sheet_name: str,
        update_fields: dict[str, str],
        expected_row_hash: str,
        max_attempts: int,
    ) -> int:
        now = _now_iso()
        with _connect(self._db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO pending_backlog_updates
                   (request_id, item_id, spreadsheet_id, sheet_name, update_fields,
                    expected_row_hash, attempt_count, max_attempts, status,
                    last_error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'pending', '', ?)""",
                (
                    request_id,
                    item_id,
                    spreadsheet_id,
                    sheet_name,
                    json.dumps(update_fields),
                    expected_row_hash,
                    max_attempts,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_pending_updates(self, *, status: str = "pending") -> list[PendingBacklogUpdate]:
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM pending_backlog_updates WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
        return [_row_to_pending_update(row) for row in rows]

    def claim_pending_update(self, update_id: int) -> PendingBacklogUpdate | None:
        """Atomically claim one pending row for a flush attempt.

        Returns None if the row is missing or no longer pending (already
        claimed/applied/abandoned elsewhere) — mirrors the atomic
        SELECT-then-UPDATE-with-rowcount-check pattern used for approval
        token consumption.
        """
        now = _now_iso()
        with _connect(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM pending_backlog_updates WHERE id = ? AND status = 'pending'",
                (update_id,),
            ).fetchone()
            if existing is None:
                return None
            updated = conn.execute(
                """UPDATE pending_backlog_updates
                   SET attempt_count = attempt_count + 1, last_attempted_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (now, update_id),
            )
            if updated.rowcount != 1:
                logger.error("Pending backlog update race detected id=%s", update_id)
                return None
            claimed = conn.execute(
                "SELECT * FROM pending_backlog_updates WHERE id = ?",
                (update_id,),
            ).fetchone()
        return _row_to_pending_update(claimed)

    def mark_applied(self, update_id: int) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE pending_backlog_updates SET status = 'applied' WHERE id = ?",
                (update_id,),
            )

    def mark_pending_failed(self, update_id: int, error: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE pending_backlog_updates SET last_error = ? WHERE id = ?",
                (error, update_id),
            )

    def mark_abandoned(self, update_id: int, error: str) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE pending_backlog_updates SET status = 'abandoned', last_error = ? "
                "WHERE id = ?",
                (error, update_id),
            )

    def record_conflict(
        self,
        *,
        request_id: str,
        item_id: str,
        spreadsheet_id: str,
        sheet_name: str,
        expected_row_hash: str,
        actual_row_hash: str,
        actual_row_data: list[str],
        attempted_update_fields: dict[str, str],
    ) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO sync_conflicts
                   (request_id, item_id, spreadsheet_id, sheet_name, expected_row_hash,
                    actual_row_hash, actual_row_data, attempted_update_fields,
                    detected_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
                (
                    request_id,
                    item_id,
                    spreadsheet_id,
                    sheet_name,
                    expected_row_hash,
                    actual_row_hash,
                    json.dumps(actual_row_data),
                    json.dumps(attempted_update_fields),
                    _now_iso(),
                ),
            )


def enqueue_and_flush_update(
    *,
    runtime_store: BacklogRuntimeStore,
    sheets_repository: SheetsBacklogRepository,
    request_id: str,
    item_id: str,
    update_fields: dict[str, str],
    expected_row_hash: str,
    max_attempts: int,
) -> str:
    """Write-ahead an update, then attempt one immediate flush.

    Returns one of: "synced", "pending" (Sheets call failed, will retry),
    "conflict" (source row changed since the snapshot), "abandoned"
    (Sheets call failed and max_attempts was already exhausted).
    """
    reference = sheets_repository.reference
    update_id = runtime_store.enqueue_pending_update(
        request_id=request_id,
        item_id=item_id,
        spreadsheet_id=reference.spreadsheet_id,
        sheet_name=reference.sheet_name,
        update_fields=update_fields,
        expected_row_hash=expected_row_hash,
        max_attempts=max_attempts,
    )
    return _attempt_flush(runtime_store, sheets_repository, update_id)


def recover_pending_backlog_updates(
    runtime_store: BacklogRuntimeStore,
    sheets_repository_factory: Callable[[str, str], SheetsBacklogRepository],
    *,
    max_per_call: int = 20,
) -> list[str]:
    """Bounded recovery pass over pending updates, across any spreadsheet.

    sheets_repository_factory(spreadsheet_id, sheet_name) builds the
    repository for each row's own spreadsheet — pending rows may span
    multiple projects. Never retries unboundedly: each row carries its own
    max_attempts, and this call only processes up to max_per_call rows.
    """
    pending = runtime_store.list_pending_updates(status="pending")[:max_per_call]
    outcomes = []
    for update in pending:
        repository = sheets_repository_factory(update.spreadsheet_id, update.sheet_name)
        outcomes.append(_attempt_flush(runtime_store, repository, update.id))
    return outcomes


def run_pending_backlog_recovery(settings: Any, *, max_per_call: int = 20) -> list[str]:
    """Settings-driven entry point for the bounded recovery pass.

    Used by the `backlog-sync-recover` CLI command and automatically at
    Telegram-operator startup. Builds a repository per pending row's own
    spreadsheet, so recovery works across every project a caller has ever
    referenced, not just this process's own configured backlog.
    """
    store = BacklogRuntimeStore()

    def factory(spreadsheet_id: str, sheet_name: str) -> SheetsBacklogRepository:
        return repository_for(
            spreadsheet_id,
            sheet_name,
            credentials_path=settings.backlog_google_credentials_path,
        )

    return recover_pending_backlog_updates(store, factory, max_per_call=max_per_call)


def _attempt_flush(
    runtime_store: BacklogRuntimeStore,
    sheets_repository: SheetsBacklogRepository,
    update_id: int,
) -> str:
    claimed = runtime_store.claim_pending_update(update_id)
    if claimed is None:
        return "pending"

    try:
        current = sheets_repository.get_item_with_source(claimed.item_id)
    except Exception as exc:
        return _handle_flush_failure(runtime_store, claimed, str(exc))

    if current.row_hash != claimed.expected_row_hash:
        runtime_store.record_conflict(
            request_id=claimed.request_id,
            item_id=claimed.item_id,
            spreadsheet_id=claimed.spreadsheet_id,
            sheet_name=claimed.sheet_name,
            expected_row_hash=claimed.expected_row_hash,
            actual_row_hash=current.row_hash,
            actual_row_data=current.row_values,
            attempted_update_fields=claimed.update_fields,
        )
        runtime_store.mark_abandoned(
            claimed.id, "source row changed since snapshot; conflict recorded"
        )
        logger.warning(
            "Backlog sync conflict item_id=%s request_id=%s: source row changed since snapshot",
            claimed.item_id,
            claimed.request_id,
        )
        return "conflict"

    try:
        sheets_repository.apply_fields(claimed.item_id, claimed.update_fields)
    except Exception as exc:
        return _handle_flush_failure(runtime_store, claimed, str(exc))

    runtime_store.mark_applied(claimed.id)
    return "synced"


def _handle_flush_failure(
    runtime_store: BacklogRuntimeStore,
    claimed: PendingBacklogUpdate,
    error: str,
) -> str:
    if claimed.attempt_count >= claimed.max_attempts:
        runtime_store.mark_abandoned(claimed.id, error)
        logger.error(
            "Pending backlog update abandoned id=%s item_id=%s after %s attempts: %s",
            claimed.id,
            claimed.item_id,
            claimed.attempt_count,
            error,
        )
        return "abandoned"
    runtime_store.mark_pending_failed(claimed.id, error)
    logger.warning(
        "Pending backlog update flush failed id=%s item_id=%s attempt=%s/%s: %s",
        claimed.id,
        claimed.item_id,
        claimed.attempt_count,
        claimed.max_attempts,
        error,
    )
    return "pending"
