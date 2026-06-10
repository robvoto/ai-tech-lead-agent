"""Backlog repository boundary for current Markdown storage.

The graph and Telegram adapter should not know whether backlog data lives in
Markdown today or Google Sheets later. This module is the current storage edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from ai_tech_lead.config import PROJECT_ROOT


BACKLOG_ITEM_HEADING_PATTERN = re.compile(r"^## (?P<item_id>[A-Z]+-\d{3}) - (?P<title>.+)$")


@dataclass(frozen=True)
class BacklogItem:
    """One parsed backlog item."""

    item_id: str
    title: str
    body: str


@dataclass(frozen=True)
class BacklogDraft:
    """Validated fields needed before appending a backlog item."""

    item_id: str
    title: str
    approval_required: bool
    approval_reason: str
    goal: str
    constraints: list[str]


class BacklogValidationError(ValueError):
    """Raised when a backlog draft is incomplete or unsafe to append."""


class MarkdownBacklogRepository:
    """Markdown-backed backlog repository.

    This class is intentionally small so a future Google Sheets implementation
    can provide the same methods without changing Telegram or graph code.
    """

    def __init__(self, backlog_path: Path) -> None:
        self._backlog_path = _resolve_project_path(backlog_path)

    @property
    def backlog_path(self) -> Path:
        return self._backlog_path

    def list_items(self) -> list[BacklogItem]:
        if not self._backlog_path.exists():
            raise FileNotFoundError(f"Backlog file not found: {self._backlog_path}")

        text = self._backlog_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        items: list[BacklogItem] = []
        current_heading: str | None = None
        current_body: list[str] = []

        for line in lines:
            if line.startswith("## "):
                if current_heading is not None:
                    items.append(_build_item(current_heading, current_body))
                current_heading = line.removeprefix("## ").strip()
                current_body = []
                continue

            if current_heading is not None:
                current_body.append(line)

        if current_heading is not None:
            items.append(_build_item(current_heading, current_body))

        if not items:
            raise ValueError(f"No backlog items found in {self._backlog_path}")

        return items

    def get_item(self, item_id: str) -> BacklogItem:
        requested_id = item_id.strip().lower()
        items = self.list_items()
        for item in items:
            if item.item_id.lower() == requested_id:
                return item

        available_ids = ", ".join(item.item_id for item in items)
        raise ValueError(
            f"Backlog item '{item_id}' was not found. Available IDs: {available_ids}"
        )

    def next_item_id(self, prefix: str = "ATL") -> str:
        normalized_prefix = prefix.strip().upper()
        if not re.fullmatch(r"[A-Z]+", normalized_prefix):
            raise BacklogValidationError("Backlog ID prefix must contain uppercase letters only.")

        highest_number = 0
        for item in self.list_items():
            match = re.fullmatch(rf"{re.escape(normalized_prefix)}-(\d{{3}})", item.item_id)
            if match:
                highest_number = max(highest_number, int(match.group(1)))

        return f"{normalized_prefix}-{highest_number + 1:03d}"

    def add_item(self, draft: BacklogDraft) -> BacklogItem:
        validate_backlog_draft(draft, existing_ids={item.item_id for item in self.list_items()})
        rendered_item = render_backlog_draft(draft)
        existing_text = self._backlog_path.read_text(encoding="utf-8").rstrip()
        self._backlog_path.write_text(
            f"{existing_text}\n\n{rendered_item}\n",
            encoding="utf-8",
        )
        return self.get_item(draft.item_id)


def validate_backlog_draft(
    draft: BacklogDraft,
    *,
    existing_ids: set[str] | None = None,
) -> None:
    """Validate a draft before it can be appended to the backlog."""

    if not re.fullmatch(r"[A-Z]+-\d{3}", draft.item_id):
        raise BacklogValidationError("Backlog item ID must look like ATL-010.")
    if existing_ids and draft.item_id in existing_ids:
        raise BacklogValidationError(f"Backlog item ID already exists: {draft.item_id}")
    if not draft.title.strip():
        raise BacklogValidationError("Backlog title is required.")
    if len(draft.title.strip()) > 120:
        raise BacklogValidationError("Backlog title must be 120 characters or less.")
    if not draft.approval_reason.strip():
        raise BacklogValidationError("Approval reason is required.")
    if not draft.goal.strip():
        raise BacklogValidationError("Goal is required.")
    if not draft.constraints or any(not item.strip() for item in draft.constraints):
        raise BacklogValidationError("At least one non-empty constraint is required.")


def render_backlog_draft(draft: BacklogDraft) -> str:
    validate_backlog_draft(draft)
    approval_required_text = "yes" if draft.approval_required else "no"
    constraints = "\n".join(f"- {constraint.strip()}" for constraint in draft.constraints)
    return "\n".join(
        [
            f"## {draft.item_id} - {draft.title.strip()}",
            "",
            f"Approval Required: {approval_required_text}",
            f"Approval Reason: {draft.approval_reason.strip()}",
            "",
            "Goal:",
            draft.goal.strip(),
            "",
            "Constraints:",
            constraints,
        ]
    )


def _build_item(heading: str, body_lines: list[str]) -> BacklogItem:
    if " - " in heading:
        item_id, title = heading.split(" - ", 1)
    else:
        item_id = "UNKNOWN"
        title = heading

    return BacklogItem(
        item_id=item_id.strip(),
        title=title.strip(),
        body="\n".join(body_lines).strip(),
    )


def _resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
