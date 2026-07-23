"""One-time approval tokens for JSON subprocess human-approval flow.

When the workflow returns approval_required, we issue a UUID token and store it
locally together with the request_id and a digest of the task text. The caller
relays it to the human. When the human approves, the caller resubmits with
human_approved=true and the token. We validate the same request/task pair,
consume the token (one-time use), then allow execution.

This prevents a rogue caller from setting human_approved=true without an actual
human ever having approved anything.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
import uuid

from ai_tech_lead.config import DATA_DIR
from ai_tech_lead.logging_setup import LOGGER_NAME

APPROVALS_DB = DATA_DIR / "pending_approvals.sqlite3"
TOKEN_TTL_SECONDS = 3600  # tokens expire after 1 hour
SQLITE_BUSY_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger(LOGGER_NAME)

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS pending_approvals (
        token      TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        task_digest TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    )
"""


def create_approval_token(request_id: str, task_text: str) -> str:
    """Issue a one-time approval token bound to a specific request/task pair."""
    token = str(uuid.uuid4())
    now = int(time.time())
    expires_at = now + TOKEN_TTL_SECONDS
    task_digest = _task_digest(task_text)
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)
        conn.execute(
            "INSERT INTO pending_approvals VALUES (?,?,?,?,?)",
            (token, request_id, task_digest, now, expires_at),
        )
    logger.info(
        "Issued approval token request_id=%s token_prefix=%s expires_at=%s",
        request_id,
        token[:8],
        expires_at,
    )
    return token


def consume_approval_token(token: str, request_id: str, task_text: str) -> bool:
    """Validate and permanently consume a token.

    Returns True if the token exists, has not expired, and matches the request/task pair.
    Returns False if the token is missing, already used, expired, or bound to a different task.
    """
    if not token or not token.strip():
        return False
    if not request_id or not request_id.strip():
        return False
    if not task_text or not task_text.strip():
        return False

    expected_task_digest = _task_digest(task_text)
    now = int(time.time())
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT request_id, task_digest, expires_at FROM pending_approvals WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            logger.warning(
                "Approval token rejected: missing token_prefix=%s request_id=%s",
                token[:8],
                request_id,
            )
            return False
        stored_request_id, stored_task_digest, expires_at = row
        if expires_at < now:
            conn.execute("DELETE FROM pending_approvals WHERE token = ?", (token,))
            logger.warning(
                "Approval token rejected: expired token_prefix=%s request_id=%s",
                token[:8],
                request_id,
            )
            return False
        if stored_request_id != request_id or stored_task_digest != expected_task_digest:
            logger.warning(
                "Approval token rejected: request/task mismatch token_prefix=%s request_id=%s",
                token[:8],
                request_id,
            )
            return False
        deleted = conn.execute(
            "DELETE FROM pending_approvals WHERE token = ?",
            (token,),
        )
        if deleted.rowcount != 1:
            logger.error(
                "Approval token race detected token_prefix=%s request_id=%s",
                token[:8],
                request_id,
            )
            return False
    logger.info("Consumed approval token request_id=%s token_prefix=%s", request_id, token[:8])
    return True


def _connect() -> sqlite3.Connection:
    APPROVALS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(APPROVALS_DB), timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
    return conn


def _task_digest(task_text: str) -> str:
    normalized_task = task_text.strip()
    return hashlib.sha256(normalized_task.encode("utf-8")).hexdigest()
