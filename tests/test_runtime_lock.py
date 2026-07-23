from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import ai_tech_lead.runtime_lock as runtime_lock
from ai_tech_lead.runtime_lock import (
    RuntimeLockBusyError,
    acquire_project_execution_lock,
    acquire_request_run_lock,
    list_active_project_execution_locks,
)


def test_request_run_lock_rejects_duplicate_request_id() -> None:
    first = acquire_request_run_lock("req-123")

    try:
        with pytest.raises(RuntimeLockBusyError, match="request-run"):
            acquire_request_run_lock("req-123")
    finally:
        first.release()


def test_project_execution_lock_rejects_duplicate_project_root(tmp_path: Path) -> None:
    first = acquire_project_execution_lock(tmp_path)

    try:
        with pytest.raises(RuntimeLockBusyError, match="project-execution"):
            acquire_project_execution_lock(tmp_path)
    finally:
        first.release()


def test_list_active_project_execution_locks_prunes_stale_lock(tmp_path: Path) -> None:
    runtime_lock.RUNTIME_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    stale_path = runtime_lock.RUNTIME_LOCKS_DIR / "project-execution-stale.lock.json"
    stale_path.write_text(
        json.dumps(
            {
                "scope": runtime_lock.PROJECT_EXECUTION_SCOPE,
                "key": str(tmp_path),
                "pid": 999999,
                "hostname": "test-host",
                "created_at": 0,
                "owner_token": "stale",
                "context": {"project_root": str(tmp_path)},
            }
        ),
        encoding="utf-8",
    )

    active = list_active_project_execution_locks()

    assert active == []
    assert not stale_path.exists()


def test_request_lock_replaces_stale_holder_file() -> None:
    runtime_lock.RUNTIME_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    stale_path = runtime_lock.RUNTIME_LOCKS_DIR / "request-run-stale.lock.json"
    stale_path.write_text(
        json.dumps(
            {
                "scope": runtime_lock.REQUEST_RUN_SCOPE,
                "key": "req-456",
                "pid": 999999,
                "hostname": "test-host",
                "created_at": 0,
                "owner_token": "stale",
                "context": {"request_id": "req-456"},
            }
        ),
        encoding="utf-8",
    )

    lease = acquire_request_run_lock("req-456")

    try:
        assert lease.lock_path.exists()
        payload = json.loads(lease.lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
    finally:
        lease.release()
