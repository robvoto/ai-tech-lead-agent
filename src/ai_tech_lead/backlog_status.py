"""Canonical backlog status values used across the local prototype."""

from __future__ import annotations

from enum import StrEnum


class BacklogStatus(StrEnum):
    """Fixed backlog statuses used by the repository and Telegram tools."""

    BACKLOG = "Backlog"
    NOT_DONE = "Not Done"
    IN_PROGRESS = "In Progress"
    NEEDS_REVIEW = "Needs Review"
    BLOCKED = "Blocked"
    DONE = "Done"
    WONT_DO = "Won't Do"
    OBSOLETE = "Obsolete"


_STATUS_BY_NUMBER: dict[str, BacklogStatus] = {
    str(i + 1): status for i, status in enumerate(BacklogStatus)
}


def normalize_backlog_status(value: str) -> BacklogStatus | None:
    """Return the canonical status for a user-provided value, or ``None``.

    Accepts a number (1–8) as a shorthand for the status at that position.
    """

    stripped = value.strip()
    if stripped in _STATUS_BY_NUMBER:
        return _STATUS_BY_NUMBER[stripped]
    normalized = " ".join(stripped.split()).lower()
    for status in BacklogStatus:
        if status.value.lower() == normalized:
            return status
    return None


def backlog_status_choices() -> str:
    """Return a human-readable numbered list of allowed backlog statuses."""

    return ", ".join(
        f"{i + 1}={status.value}" for i, status in enumerate(BacklogStatus)
    )


def open_backlog_statuses() -> tuple[BacklogStatus, ...]:
    """Return the statuses that are still eligible for selection."""

    return (
        BacklogStatus.BACKLOG,
        BacklogStatus.NOT_DONE,
        BacklogStatus.IN_PROGRESS,
        BacklogStatus.NEEDS_REVIEW,
        BacklogStatus.BLOCKED,
    )
