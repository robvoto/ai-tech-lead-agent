"""Small cross-process runtime locks for subprocess safety.

These locks are used to make concurrency failures explicit instead of allowing
silent overlap on the same logical resource.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_tech_lead.config import DATA_DIR
from ai_tech_lead.logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

RUNTIME_LOCKS_DIR = DATA_DIR / "runtime_locks"
REQUEST_RUN_SCOPE = "request-run"
PROJECT_EXECUTION_SCOPE = "project-execution"


class RuntimeLockBusyError(RuntimeError):
    """Raised when another process already holds a runtime lock."""


@dataclass
class RuntimeLockLease:
    scope: str
    key: str
    lock_path: Path
    owner_token: str
    released: bool = False

    def release(self) -> None:
        if self.released:
            return

        metadata = _read_lock_metadata(self.lock_path)
        if metadata is None:
            logger.warning(
                "Runtime lock file disappeared before release scope=%s key=%s path=%s",
                self.scope,
                self.key,
                self.lock_path,
            )
            self.released = True
            return

        if metadata.get("owner_token") != self.owner_token:
            logger.warning(
                "Runtime lock owner changed before release scope=%s key=%s path=%s",
                self.scope,
                self.key,
                self.lock_path,
            )
            self.released = True
            return

        self.lock_path.unlink(missing_ok=True)
        self.released = True
        logger.info(
            "Released runtime lock scope=%s key=%s path=%s",
            self.scope,
            self.key,
            self.lock_path,
        )

    def __enter__(self) -> RuntimeLockLease:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_request_run_lock(request_id: str) -> RuntimeLockLease:
    return acquire_runtime_lock(REQUEST_RUN_SCOPE, request_id, context={"request_id": request_id})


def acquire_project_execution_lock(project_root: Path) -> RuntimeLockLease:
    resolved = str(project_root.resolve())
    return acquire_runtime_lock(
        PROJECT_EXECUTION_SCOPE,
        resolved,
        context={"project_root": resolved},
    )


def list_active_project_execution_locks() -> list[dict[str, Any]]:
    return list_active_runtime_locks(PROJECT_EXECUTION_SCOPE)


def list_active_runtime_locks(scope: str | None = None) -> list[dict[str, Any]]:
    RUNTIME_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    active: list[dict[str, Any]] = []
    pattern = "*.lock.json" if scope is None else f"{scope}-*.lock.json"
    for path in RUNTIME_LOCKS_DIR.glob(pattern):
        metadata = _read_lock_metadata(path)
        if metadata is None:
            continue
        holder_pid = metadata.get("pid")
        if not isinstance(holder_pid, int) or not _pid_is_running(holder_pid):
            logger.warning("Removing stale runtime lock path=%s holder_pid=%s", path, holder_pid)
            path.unlink(missing_ok=True)
            continue
        active.append(metadata)
    return active


def acquire_runtime_lock(
    scope: str,
    key: str,
    *,
    context: dict[str, Any] | None = None,
) -> RuntimeLockLease:
    RUNTIME_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(scope, key)
    owner_token = uuid.uuid4().hex
    metadata = {
        "scope": scope,
        "key": key,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": int(time.time()),
        "owner_token": owner_token,
        "context": context or {},
    }

    for _attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            holder = _read_lock_metadata(lock_path)
            if holder is not None:
                holder_pid = holder.get("pid")
                if isinstance(holder_pid, int) and not _pid_is_running(holder_pid):
                    logger.warning(
                        "Removing stale runtime lock scope=%s key=%s path=%s holder_pid=%s",
                        scope,
                        key,
                        lock_path,
                        holder_pid,
                    )
                    lock_path.unlink(missing_ok=True)
                    continue

            message = _busy_lock_message(scope, key, holder)
            logger.error(message)
            raise RuntimeLockBusyError(message) from None

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, sort_keys=True)

        logger.info("Acquired runtime lock scope=%s key=%s path=%s", scope, key, lock_path)
        return RuntimeLockLease(scope=scope, key=key, lock_path=lock_path, owner_token=owner_token)

    message = _busy_lock_message(scope, key, None)
    logger.error(message)
    raise RuntimeLockBusyError(message)


def _lock_path(scope: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return RUNTIME_LOCKS_DIR / f"{scope}-{digest}.lock.json"


def _read_lock_metadata(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return {"scope": "unknown", "key": str(path), "pid": None}
    return payload if isinstance(payload, dict) else None


def _busy_lock_message(scope: str, key: str, holder: dict[str, Any] | None) -> str:
    if holder is None:
        return f"Runtime lock busy for {scope}: {key}"

    holder_pid = holder.get("pid")
    holder_host = holder.get("hostname", "unknown-host")
    created_at = holder.get("created_at", "unknown-time")
    return (
        f"Runtime lock busy for {scope}: {key}. "
        f"Held by pid={holder_pid} host={holder_host} created_at={created_at}."
    )


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
