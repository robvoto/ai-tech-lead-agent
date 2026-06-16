"""SQLite-backed backlog repository.

Replaces the Excel workbook path with a persistent SQLite store.
On first use it migrates any existing Excel data (best-effort, silent if missing).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Generator

from .backlog_repository import (
    BacklogDraft,
    BacklogItem,
    BacklogRefinementDraft,
    BacklogValidationError,
    _backlog_item_listing_key,
    format_backlog_list_item,  # re-export so callers don't need two imports
    open_backlog_statuses,
    render_backlog_draft,
    render_backlog_refinement_draft,
    validate_backlog_draft,
    validate_backlog_refinement_draft,
)
from .backlog_status import BacklogStatus, backlog_status_choices, normalize_backlog_status
from .config import DATA_DIR, ensure_project_dirs

logger = logging.getLogger(__name__)

BACKLOG_DB_PATH = DATA_DIR / "backlog.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backlog_items (
    item_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    complexity TEXT NOT NULL DEFAULT '',
    created_date TEXT NOT NULL DEFAULT '',
    interrupt_before_implementation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Backlog'
);
"""


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


def _row_to_item(row: sqlite3.Row) -> BacklogItem:
    return BacklogItem(
        item_id=row["item_id"],
        title=row["title"],
        body=row["body"],
        priority=row["priority"],
        complexity=row["complexity"],
        created_date=row["created_date"],
        interrupt_before_implementation=bool(row["interrupt_before_implementation"]),
        status=normalize_backlog_status(row["status"]) or BacklogStatus.BACKLOG,
    )


def _item_to_row(item: BacklogItem) -> dict:
    return {
        "item_id": item.item_id,
        "title": item.title,
        "body": item.body,
        "priority": item.priority,
        "complexity": item.complexity,
        "created_date": item.created_date,
        "interrupt_before_implementation": int(item.interrupt_before_implementation),
        "status": item.status.value,
    }


class SqliteBacklogRepository:
    """SQLite-backed backlog repository with the same public interface as MarkdownBacklogRepository."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or BACKLOG_DB_PATH
        ensure_project_dirs()
        self._ensure_migrated()

    def _ensure_migrated(self) -> None:
        with _connect(self._db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM backlog_items").fetchone()[0]
        if count == 0:
            self._migrate_from_excel()

    def _migrate_from_excel(self) -> None:
        """One-time migration from the Excel workbook if it exists."""
        try:
            from .backlog_repository import MarkdownBacklogRepository
            from .config import PROJECT_ROOT

            excel_path = PROJECT_ROOT / "data" / "backlog" / "ai_tech_lead_backlog.xlsx"
            if not excel_path.exists():
                return
            repo = MarkdownBacklogRepository(excel_path)
            items = repo.list_items()
            if not items:
                return
            with _connect(self._db_path) as conn:
                for item in items:
                    row = _item_to_row(item)
                    conn.execute(
                        """INSERT OR IGNORE INTO backlog_items
                           (item_id, title, body, priority, complexity, created_date,
                            interrupt_before_implementation, status)
                           VALUES (:item_id, :title, :body, :priority, :complexity,
                                   :created_date, :interrupt_before_implementation, :status)""",
                        row,
                    )
            logger.info("Migrated %d backlog items from Excel to SQLite.", len(items))
        except Exception as exc:
            logger.warning("Excel migration skipped: %s", exc)

    def list_items(self) -> list[BacklogItem]:
        with _connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM backlog_items").fetchall()
        if not rows:
            raise ValueError(f"No backlog items found in {self._db_path}")
        return [_row_to_item(r) for r in rows]

    def list_open_items(self) -> list[BacklogItem]:
        return [i for i in self.list_items() if i.status in open_backlog_statuses()]

    def list_items_sorted(self) -> list[BacklogItem]:
        return sorted(self.list_items(), key=_backlog_item_listing_key)

    def list_open_items_sorted(self) -> list[BacklogItem]:
        return sorted(self.list_open_items(), key=_backlog_item_listing_key)

    def get_item(self, item_id: str) -> BacklogItem:
        requested = item_id.strip().lower()
        with _connect(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM backlog_items").fetchall()
        for row in rows:
            if row["item_id"].lower() == requested:
                return _row_to_item(row)
        available = ", ".join(r["item_id"] for r in rows)
        raise ValueError(f"Backlog item '{item_id}' was not found. Available IDs: {available}")

    def get_item_for_execution(self, item_id: str) -> BacklogItem:
        item = self.get_item(item_id)
        if item.status in {BacklogStatus.DONE, BacklogStatus.WONT_DO, BacklogStatus.OBSOLETE}:
            raise ValueError(
                f"Backlog item '{item.item_id}' is Done and cannot be selected for execution."
            )
        return item

    def next_item_id(self, prefix: str = "ATL") -> str:
        normalized_prefix = prefix.strip().upper()
        if not re.fullmatch(r"[A-Z]+", normalized_prefix):
            raise BacklogValidationError("Backlog ID prefix must contain uppercase letters only.")
        highest = 0
        with _connect(self._db_path) as conn:
            rows = conn.execute("SELECT item_id FROM backlog_items").fetchall()
        for row in rows:
            m = re.fullmatch(rf"{re.escape(normalized_prefix)}-(\d{{3}})", row["item_id"])
            if m:
                highest = max(highest, int(m.group(1)))
        return f"{normalized_prefix}-{highest + 1:03d}"

    def add_item(self, draft: BacklogDraft) -> BacklogItem:
        validate_backlog_draft(draft, existing_ids={i.item_id for i in self.list_items()})
        rendered_body = render_backlog_draft(draft)
        item = BacklogItem(
            item_id=draft.item_id,
            title=draft.title.strip(),
            body=rendered_body,
            priority=draft.priority,
            interrupt_before_implementation=draft.approval_required,
            status=BacklogStatus.BACKLOG,
            created_date=date.today().isoformat(),
        )
        with _connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO backlog_items
                   (item_id, title, body, priority, complexity, created_date,
                    interrupt_before_implementation, status)
                   VALUES (:item_id, :title, :body, :priority, :complexity,
                           :created_date, :interrupt_before_implementation, :status)""",
                _item_to_row(item),
            )
        return self.get_item(draft.item_id)

    def update_item_status(self, item_id: str, new_status: str) -> BacklogItem:
        normalized_id = item_id.strip().upper()
        self.get_item(normalized_id)
        if not new_status.strip():
            raise ValueError("Status cannot be empty.")
        status_value = normalize_backlog_status(new_status)
        if status_value is None:
            raise ValueError(f"Status must be one of: {backlog_status_choices()}.")
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE backlog_items SET status=? WHERE UPPER(item_id)=?",
                (status_value.value, normalized_id),
            )
        return self.get_item(normalized_id)

    def complete_item(self, item_id: str, validation_note: str) -> BacklogItem:
        normalized_id = item_id.strip().upper()
        item = self.get_item(normalized_id)
        updated_body = _inject_validation_into_body(item.body, validation_note)
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE backlog_items SET status=?, body=? WHERE UPPER(item_id)=?",
                (BacklogStatus.DONE.value, updated_body, normalized_id),
            )
        return self.get_item(normalized_id)

    def add_refined_item(self, draft: BacklogRefinementDraft) -> BacklogItem:
        validate_backlog_refinement_draft(
            draft,
            existing_ids={i.item_id for i in self.list_items()},
        )
        rendered_body = render_backlog_refinement_draft(draft)
        item = BacklogItem(
            item_id=draft.item_id,
            title=draft.title.strip(),
            body=rendered_body,
            priority=draft.priority,
            complexity=getattr(draft, "size", ""),
            created_date=date.today().isoformat(),
            interrupt_before_implementation=draft.approval_required,
            status=BacklogStatus.BACKLOG,
        )
        with _connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO backlog_items
                   (item_id, title, body, priority, complexity, created_date,
                    interrupt_before_implementation, status)
                   VALUES (:item_id, :title, :body, :priority, :complexity,
                           :created_date, :interrupt_before_implementation, :status)""",
                _item_to_row(item),
            )
        return self.get_item(draft.item_id)


def _inject_validation_into_body(body: str, validation_note: str) -> str:
    """Insert or replace a Validation: line in the body, after Status: if present."""
    lines = body.splitlines(keepends=True)
    status_idx = None
    validation_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith("status:"):
            status_idx = i
        if stripped.lower().startswith("validation:"):
            validation_idx = i

    validation_line = f"Validation: {validation_note.strip()}\n"
    if validation_idx is not None:
        lines[validation_idx] = validation_line
    elif status_idx is not None:
        lines.insert(status_idx + 1, validation_line)
    else:
        lines.insert(0, validation_line)

    return "".join(lines)
