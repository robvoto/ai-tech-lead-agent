"""Bounded durable audit summaries for coding runs.

LangGraph checkpoints remain the source of resumable workflow state. This store
keeps only a compact receipt: enough to inspect what happened without copying
checkpoint payloads, prompts, provider traces, or instruction files.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import DB_PATH

RUN_AUDIT_DB_PATH = DB_PATH
AUDIT_SCHEMA_VERSION = 1
_MAX_TEXT_CHARS = 800
_MAX_ITEMS = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_audit_summaries (
    request_id                    TEXT PRIMARY KEY,
    thread_id                     TEXT NOT NULL,
    task                          TEXT NOT NULL,
    model                         TEXT NOT NULL,
    profile                       TEXT NOT NULL,
    approval_required             INTEGER,
    approved                      INTEGER,
    approved_by                   TEXT NOT NULL,
    approval_action               TEXT NOT NULL,
    approval_revision_count       INTEGER NOT NULL,
    git_branch                    TEXT NOT NULL,
    git_head                      TEXT NOT NULL,
    git_dirty                     INTEGER,
    git_dirty_paths               TEXT NOT NULL,
    tools_used                    TEXT NOT NULL,
    files_used                    TEXT NOT NULL,
    selected_skill_paths          TEXT NOT NULL,
    selected_skill_hashes         TEXT NOT NULL,
    project_guidance_paths        TEXT NOT NULL,
    project_guidance_hash         TEXT NOT NULL,
    guidance_changed_on_resume    INTEGER NOT NULL,
    rubric_status                 TEXT NOT NULL,
    result_status                 TEXT NOT NULL,
    result_kind                   TEXT NOT NULL,
    result_summary                TEXT NOT NULL,
    validation_status             TEXT NOT NULL,
    validation_reason             TEXT NOT NULL,
    tokens_in                     INTEGER,
    tokens_out                    INTEGER,
    cost_usd                      REAL,
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_audit_updated_at
    ON run_audit_summaries(updated_at);
"""

_SECRET_VALUE_RE = re.compile(
    r"(?ix)(\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|secret|credential)\b"
    r"\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([^\s,;]+)")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_PRIVATE_KEY_RE = re.compile(
    r"(?is)-----BEGIN [^-]+-----.*?-----END [^-]+-----"
)


@dataclass(frozen=True)
class RunAuditSummary:
    request_id: str
    thread_id: str
    task: str
    model: str
    profile: str
    approval_required: bool | None
    approved: bool | None
    approved_by: str
    approval_action: str
    approval_revision_count: int
    git_branch: str
    git_head: str
    git_dirty: bool | None
    git_dirty_paths: tuple[str, ...]
    tools_used: tuple[str, ...]
    files_used: tuple[str, ...]
    selected_skill_paths: tuple[str, ...]
    selected_skill_hashes: dict[str, str]
    project_guidance_paths: tuple[str, ...]
    project_guidance_hash: str
    guidance_changed_on_resume: bool
    rubric_status: str
    result_status: str
    result_kind: str
    result_summary: str
    validation_status: str
    validation_reason: str
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    created_at: str
    updated_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "task": self.task,
            "model": self.model,
            "profile": self.profile,
            "approval_required": self.approval_required,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "approval_action": self.approval_action,
            "approval_revision_count": self.approval_revision_count,
            "git": {
                "branch": self.git_branch,
                "head": self.git_head,
                "dirty": self.git_dirty,
                "dirty_paths": list(self.git_dirty_paths),
            },
            "tools_used": list(self.tools_used),
            "files_used": list(self.files_used),
            "selected_skill_paths": list(self.selected_skill_paths),
            "selected_skill_hashes": dict(self.selected_skill_hashes),
            "project_guidance_paths": list(self.project_guidance_paths),
            "project_guidance_hash": self.project_guidance_hash,
            "guidance_changed_on_resume": self.guidance_changed_on_resume,
            "rubric_status": self.rubric_status,
            "result": {
                "status": self.result_status,
                "kind": self.result_kind,
                "summary": self.result_summary,
            },
            "validation": {
                "status": self.validation_status,
                "reason": self.validation_reason,
            },
            "usage": {
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "cost_usd": self.cost_usd,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RunAuditStore:
    """Persist and retrieve one compact receipt per request_id."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or RUN_AUDIT_DB_PATH

    def ensure_initialized(self) -> None:
        with self._connect():
            pass

    def save(self, summary: RunAuditSummary) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO run_audit_summaries (
                    request_id, thread_id, task, model, profile,
                    approval_required, approved, approved_by, approval_action,
                    approval_revision_count, git_branch, git_head, git_dirty,
                    git_dirty_paths, tools_used, files_used, selected_skill_paths,
                    selected_skill_hashes, project_guidance_paths, project_guidance_hash,
                    guidance_changed_on_resume, rubric_status, result_status, result_kind,
                    result_summary, validation_status, validation_reason, tokens_in,
                    tokens_out, cost_usd, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(request_id) DO UPDATE SET
                    thread_id=excluded.thread_id, task=excluded.task, model=excluded.model,
                    profile=excluded.profile, approval_required=excluded.approval_required,
                    approved=excluded.approved, approved_by=excluded.approved_by,
                    approval_action=excluded.approval_action,
                    approval_revision_count=excluded.approval_revision_count,
                    git_branch=excluded.git_branch, git_head=excluded.git_head,
                    git_dirty=excluded.git_dirty, git_dirty_paths=excluded.git_dirty_paths,
                    tools_used=excluded.tools_used, files_used=excluded.files_used,
                    selected_skill_paths=excluded.selected_skill_paths,
                    selected_skill_hashes=excluded.selected_skill_hashes,
                    project_guidance_paths=excluded.project_guidance_paths,
                    project_guidance_hash=excluded.project_guidance_hash,
                    guidance_changed_on_resume=excluded.guidance_changed_on_resume,
                    rubric_status=excluded.rubric_status, result_status=excluded.result_status,
                    result_kind=excluded.result_kind, result_summary=excluded.result_summary,
                    validation_status=excluded.validation_status,
                    validation_reason=excluded.validation_reason, tokens_in=excluded.tokens_in,
                    tokens_out=excluded.tokens_out, cost_usd=excluded.cost_usd,
                    updated_at=excluded.updated_at""",
                _summary_values(summary),
            )

    def get(self, request_id: str) -> RunAuditSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_audit_summaries WHERE request_id = ?",
                (request_id.strip(),),
            ).fetchone()
        return _row_to_summary(row) if row is not None else None

    def _connect(self) -> _ManagedConnection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return _ManagedConnection(conn)


class _ManagedConnection:
    """Commit/close wrapper used by the store's context manager."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def record_run_audit_summary(
    *,
    request_id: str,
    thread_id: str,
    state: Mapping[str, Any],
    result: Mapping[str, Any],
    settings: Any,
    store: RunAuditStore | None = None,
) -> RunAuditSummary:
    """Build and persist a bounded receipt from graph state and structured output."""

    now = _now_iso()
    selected_hashes = _string_mapping(state.get("selected_skill_hashes"))
    selected_paths = _bounded_items(
        state.get("selected_skill_paths") or list(selected_hashes), max_chars=240
    )
    dirty_paths = _bounded_items(state.get("git_preflight_dirty_paths"), max_chars=240)
    files_used = _bounded_items(state.get("coding_agent_changed_files"), max_chars=240)
    tools_used = _bounded_items(
        [state.get("coding_agent_performed_by"), state.get("coding_agent_command")],
        max_chars=120,
    )
    preflight_status = str(state.get("git_preflight_status", "")).strip()
    validation_status = str(state.get("verification_status", "")).strip()
    if not validation_status and preflight_status.startswith("blocked"):
        validation_status = preflight_status

    summary = RunAuditSummary(
        request_id=request_id.strip(),
        thread_id=thread_id.strip(),
        task=_bounded_text(state.get("request", "")),
        model=str(getattr(settings, "orchestrator_ai_model", "")).strip(),
        profile=str(getattr(settings, "orchestrator_ai_profile", "")).strip(),
        approval_required=_optional_bool(state, "needs_approval"),
        approved=_optional_bool(state, "approved"),
        approved_by=_bounded_text(state.get("approved_by", ""), max_chars=160),
        approval_action=_bounded_text(state.get("approval_action", ""), max_chars=80),
        approval_revision_count=_optional_int(state.get("approval_revision_count"), default=0),
        git_branch=_bounded_text(state.get("git_preflight_branch", ""), max_chars=160),
        git_head=_bounded_text(state.get("git_preflight_head", ""), max_chars=160),
        git_dirty=_git_dirty_value(state, preflight_status),
        git_dirty_paths=tuple(dirty_paths),
        tools_used=tuple(tools_used),
        files_used=tuple(files_used),
        selected_skill_paths=tuple(selected_paths),
        selected_skill_hashes=selected_hashes,
        project_guidance_paths=tuple(
            _bounded_items(state.get("project_guidance_paths"), max_chars=240)
        ),
        project_guidance_hash=_bounded_text(state.get("project_guidance_hash", ""), max_chars=128),
        guidance_changed_on_resume=bool(state.get("project_guidance_changed_on_resume", False)),
        rubric_status=_bounded_text(state.get("rubric_status", ""), max_chars=80),
        result_status=_bounded_text(result.get("status", ""), max_chars=80),
        result_kind=_bounded_text(result.get("result_kind", ""), max_chars=100),
        result_summary=_bounded_text(result.get("summary", "")),
        validation_status=_bounded_text(validation_status, max_chars=100),
        validation_reason=_bounded_text(
            state.get("verification_reason", "") or state.get("git_preflight_reason", "")
        ),
        tokens_in=_optional_int(state.get("tokens_in", result.get("tokens_in"))),
        tokens_out=_optional_int(state.get("tokens_out", result.get("tokens_out"))),
        cost_usd=_optional_float(state.get("cost_usd", result.get("cost_usd"))),
        created_at=now,
        updated_at=now,
    )
    (store or RunAuditStore()).save(summary)
    return summary


def _summary_values(summary: RunAuditSummary) -> tuple[Any, ...]:
    return (
        summary.request_id,
        summary.thread_id,
        summary.task,
        summary.model,
        summary.profile,
        _db_bool(summary.approval_required),
        _db_bool(summary.approved),
        summary.approved_by,
        summary.approval_action,
        summary.approval_revision_count,
        summary.git_branch,
        summary.git_head,
        _db_bool(summary.git_dirty),
        json.dumps(list(summary.git_dirty_paths)),
        json.dumps(list(summary.tools_used)),
        json.dumps(list(summary.files_used)),
        json.dumps(list(summary.selected_skill_paths)),
        json.dumps(summary.selected_skill_hashes, sort_keys=True),
        json.dumps(list(summary.project_guidance_paths)),
        summary.project_guidance_hash,
        int(summary.guidance_changed_on_resume),
        summary.rubric_status,
        summary.result_status,
        summary.result_kind,
        summary.result_summary,
        summary.validation_status,
        summary.validation_reason,
        summary.tokens_in,
        summary.tokens_out,
        summary.cost_usd,
        summary.created_at,
        summary.updated_at,
    )


def _row_to_summary(row: sqlite3.Row) -> RunAuditSummary:
    return RunAuditSummary(
        request_id=row["request_id"],
        thread_id=row["thread_id"],
        task=row["task"],
        model=row["model"],
        profile=row["profile"],
        approval_required=_from_db_bool(row["approval_required"]),
        approved=_from_db_bool(row["approved"]),
        approved_by=row["approved_by"],
        approval_action=row["approval_action"],
        approval_revision_count=row["approval_revision_count"],
        git_branch=row["git_branch"],
        git_head=row["git_head"],
        git_dirty=_from_db_bool(row["git_dirty"]),
        git_dirty_paths=tuple(json.loads(row["git_dirty_paths"])),
        tools_used=tuple(json.loads(row["tools_used"])),
        files_used=tuple(json.loads(row["files_used"])),
        selected_skill_paths=tuple(json.loads(row["selected_skill_paths"])),
        selected_skill_hashes=json.loads(row["selected_skill_hashes"]),
        project_guidance_paths=tuple(json.loads(row["project_guidance_paths"])),
        project_guidance_hash=row["project_guidance_hash"],
        guidance_changed_on_resume=bool(row["guidance_changed_on_resume"]),
        rubric_status=row["rubric_status"],
        result_status=row["result_status"],
        result_kind=row["result_kind"],
        result_summary=row["result_summary"],
        validation_status=row["validation_status"],
        validation_reason=row["validation_reason"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost_usd=row["cost_usd"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _bounded_text(value: Any, *, max_chars: int = _MAX_TEXT_CHARS) -> str:
    text = str(value or "").strip()
    text = _PRIVATE_KEY_RE.sub("<redacted-private-key>", text)
    text = _SECRET_VALUE_RE.sub(r"\1<redacted>", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _OPENAI_KEY_RE.sub("<redacted-api-key>", text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _bounded_items(values: Any, *, max_chars: int) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return [
        _bounded_text(value, max_chars=max_chars)
        for value in list(values)[:_MAX_ITEMS]
        if str(value).strip()
    ]


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        _bounded_text(key, max_chars=240): _bounded_text(item, max_chars=128)
        for key, item in list(value.items())[:_MAX_ITEMS]
        if str(key).strip() and str(item).strip()
    }


def _git_dirty_value(state: Mapping[str, Any], preflight_status: str) -> bool | None:
    if not preflight_status and "git_preflight_dirty_paths" not in state:
        return None
    return bool(state.get("git_preflight_dirty_paths")) or preflight_status not in {"", "clean"}


def _optional_bool(state: Mapping[str, Any], key: str) -> bool | None:
    value = state.get(key)
    return bool(value) if isinstance(value, bool) else None


def _optional_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _db_bool(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _from_db_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
