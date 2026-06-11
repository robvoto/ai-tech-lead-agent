"""Canonical backlog status values used across the local prototype."""

from __future__ import annotations

from enum import StrEnum


class BacklogStatus(StrEnum):
    """Fixed backlog statuses used by the repository and Telegram tools."""

    BACKLOG = "Backlog"
    IN_PROGRESS = "In Progress"
    DONE = "Done"


def normalize_backlog_status(value: str) -> BacklogStatus | None:
    """Return the canonical status for a user-provided value, or ``None``."""

    normalized = " ".join(value.split()).lower()
    for status in BacklogStatus:
        if status.value.lower() == normalized:
            return status
    return None


def backlog_status_choices() -> str:
    """Return a human-readable list of allowed backlog statuses."""

    return ", ".join(status.value for status in BacklogStatus)


def open_backlog_statuses() -> tuple[BacklogStatus, ...]:
    """Return the statuses that are still eligible for selection."""

    return (BacklogStatus.BACKLOG, BacklogStatus.IN_PROGRESS)
