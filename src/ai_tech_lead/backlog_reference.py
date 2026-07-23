"""Provider-neutral backlog reference model.

A BacklogReference identifies exactly one row in exactly one backlog
spreadsheet. It carries no knowledge of Agent Factory, Agent Hub, or
AI Tech Lead specifically — callers supply spreadsheet_id/sheet_name
directly, or a project_key that resolves against the local alias
registry in settings. No spreadsheet is ever hardcoded in workflow logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class BacklogReferenceError(ValueError):
    """Raised when a backlog reference is missing fields or cannot be resolved."""


@dataclass(frozen=True)
class BacklogReference:
    """Identifies one backlog item row in one spreadsheet/sheet."""

    project_key: str
    spreadsheet_id: str
    sheet_name: str
    item_id: str


def resolve_backlog_reference(raw: dict[str, Any], settings: Any) -> BacklogReference:
    """Resolve a caller-supplied reference dict into a BacklogReference.

    A reference may supply spreadsheet_id/sheet_name explicitly (works for
    any spreadsheet, no local configuration required), or supply only
    project_key and rely on settings.backlog_projects for the alias.
    """

    if not isinstance(raw, dict):
        raise BacklogReferenceError("backlog_reference must be a JSON object.")

    item_id = _clean_string(raw.get("item_id"))
    if not item_id:
        raise BacklogReferenceError("backlog_reference.item_id is required.")

    project_key = _clean_string(raw.get("project_key"))
    spreadsheet_id = _clean_string(raw.get("spreadsheet_id"))
    sheet_name = _clean_string(raw.get("sheet_name"))

    if spreadsheet_id and sheet_name:
        return BacklogReference(
            project_key=project_key or spreadsheet_id,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            item_id=item_id,
        )

    if not project_key:
        raise BacklogReferenceError(
            "backlog_reference must supply either project_key, or both "
            "spreadsheet_id and sheet_name."
        )

    registry = getattr(settings, "backlog_projects", {}) or {}
    entry = registry.get(project_key)
    if entry is None:
        raise BacklogReferenceError(
            f"backlog_reference.project_key '{project_key}' is not registered in "
            "settings.backlog_projects, and no explicit spreadsheet_id/sheet_name "
            "was supplied."
        )

    resolved_spreadsheet_id = _clean_string(entry.get("spreadsheet_id"))
    resolved_sheet_name = _clean_string(entry.get("sheet_name"))
    if not resolved_spreadsheet_id or not resolved_sheet_name:
        raise BacklogReferenceError(
            f"settings.backlog_projects['{project_key}'] must define both "
            "spreadsheet_id and sheet_name."
        )

    return BacklogReference(
        project_key=project_key,
        spreadsheet_id=resolved_spreadsheet_id,
        sheet_name=resolved_sheet_name,
        item_id=item_id,
    )


def local_backlog_reference(settings: Any, item_id: str) -> BacklogReference:
    """Build a BacklogReference for this process's own configured backlog."""

    return BacklogReference(
        project_key=settings.backlog_project_key,
        spreadsheet_id=settings.backlog_spreadsheet_id,
        sheet_name=settings.backlog_sheet_name,
        item_id=item_id,
    )


def _clean_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
