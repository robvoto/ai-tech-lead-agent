"""Backlog repository boundary for current Markdown storage.

The graph and Telegram adapter should not know whether backlog data lives in
Markdown today or Google Sheets later. This module is the current storage edge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from ai_tech_lead.backlog_status import (
    BacklogStatus,
    backlog_status_choices,
    normalize_backlog_status,
    open_backlog_statuses,
)
from ai_tech_lead.config import PROJECT_ROOT

BACKLOG_ITEM_HEADING_PATTERN = re.compile(r"^## (?P<item_id>[A-Z]+-\d{3}) - (?P<title>.+)$")


@dataclass(frozen=True)
class BacklogItem:
    """One parsed backlog item."""

    item_id: str
    title: str
    body: str
    priority: str = ""
    complexity: str = ""
    created_date: str = ""
    interrupt_before_implementation: bool = False
    status: BacklogStatus = BacklogStatus.BACKLOG


@dataclass(frozen=True)
class BacklogDraft:
    """Validated fields needed before appending a backlog item."""

    item_id: str
    title: str
    priority: str
    approval_required: bool
    approval_reason: str
    goal: str
    constraints: list[str]


@dataclass(frozen=True)
class BacklogRefinementDraft:
    """Structured backlog refinement output with research context."""

    item_id: str
    title: str
    creator: str
    item_type: str
    epic: str
    priority: str
    size: str
    approval_required: bool
    approval_reason: str
    problem: str
    desired_outcome: str
    scope: list[str]
    out_of_scope: list[str]
    acceptance_criteria: list[str]
    duplicate_check_result: str
    stale_check_result: str
    already_done_check_result: str
    research_required: bool
    research_cache_used: list[str]
    external_research_needed: bool
    recommended_implementation_pattern: str
    patterns_explicitly_rejected: list[str]
    freshness_risk: str
    implementation_guidance: str
    approval_risk_flags: list[str]


class BacklogValidationError(ValueError):
    """Raised when a backlog draft is incomplete or unsafe to append."""


@runtime_checkable
class BacklogRepositoryProtocol(Protocol):
    """Shared method surface implemented by every live backlog repository.

    Markdown (this module) and Google Sheets (backlog_sheets_repository.py)
    are interchangeable through this boundary; callers must not depend on
    which one is behind it.
    """

    def list_items(self) -> list[BacklogItem]: ...

    def list_open_items(self) -> list[BacklogItem]: ...

    def list_items_sorted(self) -> list[BacklogItem]: ...

    def list_open_items_sorted(self) -> list[BacklogItem]: ...

    def get_item(self, item_id: str) -> BacklogItem: ...

    def get_item_for_execution(self, item_id: str) -> BacklogItem: ...

    def next_item_id(self, prefix: str = "ATL") -> str: ...

    def update_item_status(self, item_id: str, new_status: str) -> BacklogItem: ...

    def complete_item(self, item_id: str, validation_note: str) -> BacklogItem: ...

    def add_refined_item(self, draft: BacklogRefinementDraft) -> BacklogItem: ...


class MarkdownBacklogRepository:
    """Markdown-backed backlog repository.

    This class is intentionally small so a future Google Sheets implementation
    can provide the same methods without changing Telegram or graph code.
    """

    def __init__(self, backlog_path: Path, *, project_root: Path | None = None) -> None:
        self._project_root = project_root or PROJECT_ROOT
        self._backlog_path = _resolve_project_path(backlog_path, self._project_root)

    @property
    def backlog_path(self) -> Path:
        return self._backlog_path

    def list_items(self) -> list[BacklogItem]:
        if not self._backlog_path.exists():
            raise FileNotFoundError(f"Backlog file not found: {self._backlog_path}")

        text = self._backlog_path.read_text(encoding="utf-8", errors="replace")
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

    def list_open_items(self) -> list[BacklogItem]:
        """Return only items that are still eligible for work."""

        return [item for item in self.list_items() if item.status in open_backlog_statuses()]

    def list_items_sorted(self) -> list[BacklogItem]:
        """Return all backlog items ordered by the current listing rule."""

        return sorted(self.list_items(), key=_backlog_item_listing_key)

    def list_open_items_sorted(self) -> list[BacklogItem]:
        """Return open backlog items ordered by the current listing rule."""

        return sorted(self.list_open_items(), key=_backlog_item_listing_key)

    def get_item(self, item_id: str) -> BacklogItem:
        requested_id = item_id.strip().lower()
        items = self.list_items()
        for item in items:
            if item.item_id.lower() == requested_id:
                return item

        available_ids = ", ".join(item.item_id for item in items)
        raise ValueError(f"Backlog item '{item_id}' was not found. Available IDs: {available_ids}")

    def get_item_for_execution(self, item_id: str) -> BacklogItem:
        """Return an item only if it is still actionable."""

        item = self.get_item(item_id)
        if item.status in {
            BacklogStatus.DONE,
            BacklogStatus.WONT_DO,
            BacklogStatus.OBSOLETE,
        }:
            raise ValueError(
                f"Backlog item '{item.item_id}' is Done and cannot be selected for execution."
            )
        return item

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
        existing_text = self._backlog_path.read_text(encoding="utf-8", errors="replace").rstrip()
        self._backlog_path.write_text(
            f"{existing_text}\n\n{rendered_item}\n",
            encoding="utf-8",
        )
        return self.get_item(draft.item_id)

    def update_item_status(self, item_id: str, new_status: str) -> BacklogItem:
        """Set or replace the Status line of an existing backlog item."""
        normalized_id = item_id.strip().upper()
        self.get_item(normalized_id)  # raises ValueError if not found

        if not new_status.strip():
            raise ValueError("Status cannot be empty.")
        status_value = normalize_backlog_status(new_status)
        if status_value is None:
            raise ValueError(f"Status must be one of: {backlog_status_choices()}.")

        text = self._backlog_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)

        heading_re = re.compile(rf"^## {re.escape(normalized_id)} - ", re.IGNORECASE)
        next_heading_re = re.compile(r"^## ")

        item_start: int | None = None
        item_end: int | None = None
        status_line_index: int | None = None

        for i, line in enumerate(lines):
            if heading_re.match(line):
                item_start = i
            elif item_start is not None:
                if next_heading_re.match(line):
                    item_end = i
                    break
                if line.strip().startswith("Status:"):
                    status_line_index = i

        if item_start is None:
            raise ValueError(f"Backlog item '{normalized_id}' not found in file.")
        if item_end is None:
            item_end = len(lines)

        if status_line_index is not None:
            lines[status_line_index] = f"Status: {status_value.value}\n"
        else:
            # Insert status line immediately after the heading
            lines.insert(item_start + 1, f"Status: {status_value.value}\n")

        self._backlog_path.write_text("".join(lines), encoding="utf-8")
        return self.get_item(normalized_id)

    def complete_item(self, item_id: str, validation_note: str) -> BacklogItem:
        """Set Status: Done and insert/replace the Validation line in one write."""
        normalized_id = item_id.strip().upper()
        self.get_item(normalized_id)

        text = self._backlog_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)

        heading_re = re.compile(rf"^## {re.escape(normalized_id)} - ", re.IGNORECASE)
        next_heading_re = re.compile(r"^## ")

        item_start: int | None = None
        item_end: int | None = None
        status_line_index: int | None = None
        validation_line_index: int | None = None

        for i, line in enumerate(lines):
            if heading_re.match(line):
                item_start = i
            elif item_start is not None:
                if next_heading_re.match(line):
                    item_end = i
                    break
                if line.strip().startswith("Status:"):
                    status_line_index = i
                if line.strip().startswith("Validation:"):
                    validation_line_index = i

        if item_start is None:
            raise ValueError(f"Backlog item '{normalized_id}' not found in file.")
        if item_end is None:
            item_end = len(lines)

        if status_line_index is not None:
            lines[status_line_index] = f"Status: {BacklogStatus.DONE.value}\n"
            insert_after = status_line_index
        else:
            lines.insert(item_start + 1, f"Status: {BacklogStatus.DONE.value}\n")
            item_end += 1
            insert_after = item_start + 1

        validation_text = f"Validation: {_normalize_validation_note(validation_note)}\n"
        if validation_line_index is not None:
            actual_index = (
                validation_line_index
                if validation_line_index <= insert_after
                else validation_line_index + (1 if status_line_index is None else 0)
            )
            lines[actual_index] = validation_text
        else:
            lines.insert(insert_after + 1, validation_text)

        self._backlog_path.write_text("".join(lines), encoding="utf-8")
        return self.get_item(normalized_id)

    def add_refined_item(self, draft: BacklogRefinementDraft) -> BacklogItem:
        validate_backlog_refinement_draft(
            draft,
            existing_ids={item.item_id for item in self.list_items()},
        )
        rendered_item = render_backlog_refinement_draft(draft)
        existing_text = self._backlog_path.read_text(encoding="utf-8", errors="replace").rstrip()
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
    if _normalize_priority(draft.priority) is None:
        raise BacklogValidationError("Backlog priority must be High, Medium, or Low.")
    if not draft.approval_reason.strip():
        raise BacklogValidationError("Approval reason is required.")
    if not draft.goal.strip():
        raise BacklogValidationError("Goal is required.")
    if not draft.constraints or any(not item.strip() for item in draft.constraints):
        raise BacklogValidationError("At least one non-empty constraint is required.")


def render_backlog_draft(draft: BacklogDraft) -> str:
    validate_backlog_draft(draft)
    approval_required_text = "yes" if draft.approval_required else "no"
    priority_text = _normalize_priority(draft.priority)
    constraints = "\n".join(f"- {constraint.strip()}" for constraint in draft.constraints)
    return "\n".join(
        [
            f"## {draft.item_id} - {draft.title.strip()}",
            "",
            f"Status: {BacklogStatus.BACKLOG.value}",
            f"Priority: {priority_text}",
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


def validate_backlog_refinement_draft(
    draft: BacklogRefinementDraft,
    *,
    existing_ids: set[str] | None = None,
) -> None:
    """Validate a refined backlog draft before it is appended to the backlog."""

    if not re.fullmatch(r"[A-Z]+-\d{3}", draft.item_id):
        raise BacklogValidationError("Backlog item ID must look like ATL-010.")
    if existing_ids and draft.item_id in existing_ids:
        raise BacklogValidationError(f"Backlog item ID already exists: {draft.item_id}")
    if not draft.title.strip():
        raise BacklogValidationError("Backlog title is required.")
    if len(draft.title.strip()) > 120:
        raise BacklogValidationError("Backlog title must be 120 characters or less.")
    if not draft.creator.strip():
        raise BacklogValidationError("Creator is required.")
    if _normalize_item_type(draft.item_type) is None:
        raise BacklogValidationError("Item type must be Story, Task, Chore, or Bug.")
    if not draft.epic.strip():
        raise BacklogValidationError("Epic is required.")
    if _normalize_priority(draft.priority) is None:
        raise BacklogValidationError("Backlog priority must be High, Medium, or Low.")
    if _normalize_size(draft.size) is None:
        raise BacklogValidationError("Size must be XS, S, M, L, or XL.")
    if not draft.approval_reason.strip():
        raise BacklogValidationError("Approval reason is required.")
    if not draft.problem.strip():
        raise BacklogValidationError("Problem is required.")
    if not draft.desired_outcome.strip():
        raise BacklogValidationError("Desired outcome is required.")
    _require_non_empty_items(draft.scope, "Scope")
    _require_non_empty_items(draft.out_of_scope, "Out of scope")
    _require_non_empty_items(draft.acceptance_criteria, "Acceptance criteria")
    if not draft.duplicate_check_result.strip():
        raise BacklogValidationError("Duplicate check result is required.")
    if not draft.stale_check_result.strip():
        raise BacklogValidationError("Stale check result is required.")
    if not draft.already_done_check_result.strip():
        raise BacklogValidationError("Already-done check result is required.")
    if draft.research_required and not draft.research_cache_used:
        raise BacklogValidationError(
            "Research cache used must be provided when research is required."
        )
    if not draft.recommended_implementation_pattern.strip():
        raise BacklogValidationError("Recommended implementation pattern is required.")
    _require_non_empty_items(draft.patterns_explicitly_rejected, "Patterns explicitly rejected")
    if not draft.freshness_risk.strip():
        raise BacklogValidationError("Freshness risk is required.")
    if not draft.implementation_guidance.strip():
        raise BacklogValidationError("Implementation guidance is required.")


def render_backlog_refinement_draft(draft: BacklogRefinementDraft) -> str:
    validate_backlog_refinement_draft(draft)
    approval_required_text = "yes" if draft.approval_required else "no"
    item_type_text = _normalize_item_type(draft.item_type)
    priority_text = _normalize_priority(draft.priority)
    size_text = _normalize_size(draft.size)
    research_required_text = "yes" if draft.research_required else "no"
    external_research_needed_text = "yes" if draft.external_research_needed else "no"
    approval_risk_flags = _format_list_section(
        draft.approval_risk_flags,
        none_text="None",
    )
    patterns_rejected = _format_list_section(draft.patterns_explicitly_rejected)
    research_cache_used = _format_list_section(
        draft.research_cache_used,
        none_text="None",
    )
    scope = _format_list_section(draft.scope)
    out_of_scope = _format_list_section(draft.out_of_scope)
    acceptance_criteria = _format_list_section(draft.acceptance_criteria)
    return "\n".join(
        [
            f"## {draft.item_id} - {draft.title.strip()}",
            "",
            f"Status: {BacklogStatus.BACKLOG.value}",
            f"Creator: {draft.creator.strip()}",
            f"Type: {item_type_text}",
            f"Epic: {draft.epic.strip()}",
            f"Priority: {priority_text}",
            f"Size: {size_text}",
            f"Approval Required: {approval_required_text}",
            f"Approval Reason: {draft.approval_reason.strip()}",
            "",
            "Problem:",
            draft.problem.strip(),
            "",
            "Desired Outcome:",
            draft.desired_outcome.strip(),
            "",
            "Scope:",
            scope,
            "",
            "Out of Scope:",
            out_of_scope,
            "",
            "Acceptance Criteria:",
            acceptance_criteria,
            "",
            "Duplicate / stale / already-done check result:",
            f"- Duplicate: {draft.duplicate_check_result.strip()}",
            f"- Stale: {draft.stale_check_result.strip()}",
            f"- Already done: {draft.already_done_check_result.strip()}",
            "",
            f"Research Required: {research_required_text}",
            "Research Cache Used:",
            research_cache_used,
            f"External Research Needed: {external_research_needed_text}",
            "",
            "Recommended Implementation Pattern:",
            draft.recommended_implementation_pattern.strip(),
            "",
            "Patterns Explicitly Rejected:",
            patterns_rejected,
            "",
            "Freshness Risk:",
            draft.freshness_risk.strip(),
            "",
            "Implementation Guidance for Future Coding Agents:",
            draft.implementation_guidance.strip(),
            "",
            "Approval / Risk Flags:",
            approval_risk_flags,
        ]
    )


def format_backlog_list_item(item: BacklogItem) -> str:
    """Return a compact, phone-friendly backlog list block."""

    approval = "no" if not item.interrupt_before_implementation else "yes"
    title_line = f"{item.item_id} - {item.title}"
    details_line = (
        f"Priority: {_display_field(item.priority)} | "
        f"Complexity: {_display_field(item.complexity)} | "
        f"Created: {_display_created_date(item.created_date)} | "
        f"Approval: {approval} | "
        f"Status: {item.status.value}"
    )
    return "\n".join([title_line, details_line])


def _build_item(heading: str, body_lines: list[str]) -> BacklogItem:
    if " - " in heading:
        item_id, title = heading.split(" - ", 1)
    else:
        item_id = "UNKNOWN"
        title = heading

    body = "\n".join(body_lines).strip()
    priority = _parse_text_field(body_lines, "Priority")
    complexity = _parse_text_field(body_lines, "Complexity")
    created_date = _parse_text_field(body_lines, "Created Date")
    interrupt_before_implementation = _parse_yes_no_field(body_lines, "Approval Required")
    if not _field_exists(body_lines, "Approval Required"):
        interrupt_before_implementation = _parse_yes_no_field(
            body_lines, "Interrupt Before Implementation"
        )
    status = _parse_status_field(body_lines)
    return BacklogItem(
        item_id=item_id.strip(),
        title=title.strip(),
        body=body,
        priority=priority,
        complexity=complexity,
        created_date=created_date,
        interrupt_before_implementation=interrupt_before_implementation,
        status=status,
    )


def _parse_yes_no_field(body_lines: list[str], field_name: str) -> bool:
    prefix = f"{field_name}:"
    for line in body_lines:
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            value = stripped[len(prefix) :].strip().lower()
            return value == "yes"
    return False


def _parse_text_field(body_lines: list[str], field_name: str) -> str:
    prefix = f"{field_name}:"
    for line in body_lines:
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            return stripped[len(prefix) :].strip()
    return ""


def _field_exists(body_lines: list[str], field_name: str) -> bool:
    prefix = f"{field_name}:"
    for line in body_lines:
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            return True
    return False


def _parse_status_field(body_lines: list[str]) -> BacklogStatus:
    prefix = "Status:"
    for line in body_lines:
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            raw_value = stripped[len(prefix) :].strip()
            status = normalize_backlog_status(raw_value)
            if status is None:
                allowed_statuses = backlog_status_choices()
                raise BacklogValidationError(
                    f"Unknown backlog status '{raw_value}'. "
                    f"Allowed statuses: {allowed_statuses}."
                )
            return status
    return BacklogStatus.BACKLOG


def _backlog_item_listing_key(item: BacklogItem) -> tuple[int, int, int, date, str]:
    return (
        _priority_rank(item.priority),
        0 if not item.interrupt_before_implementation else 1,
        _complexity_rank(item.complexity),
        _created_date_sort_value(item.created_date),
        item.item_id.upper(),
    )


def _priority_rank(priority: str) -> int:
    return _rank_value(priority, {"high": 0, "medium": 1, "low": 2})


def _complexity_rank(complexity: str) -> int:
    return _rank_value(complexity, {"low": 0, "medium": 1, "high": 2})


def _rank_value(value: str, order: dict[str, int]) -> int:
    normalized = " ".join(value.split()).lower()
    return order.get(normalized, len(order))


def _created_date_sort_value(value: str) -> date:
    normalized = " ".join(value.split())
    if not normalized:
        return date.max
    for candidate in (normalized, normalized.split(" ", 1)[0]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return date.max


def _display_field(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized or "Unknown"


def _display_created_date(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        return "Unknown"
    for candidate in (normalized, normalized.split(" ", 1)[0]):
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            continue
    return normalized


def _require_non_empty_items(items: list[str], label: str) -> None:
    if not items or any(not item.strip() for item in items):
        raise BacklogValidationError(f"{label} must contain at least one non-empty item.")


def _format_list_section(items: list[str], *, none_text: str = "None") -> str:
    if not items:
        return f"- {none_text}"
    return "\n".join(f"- {item.strip()}" for item in items)


def _normalize_priority(priority: str) -> str | None:
    normalized = " ".join(priority.split()).lower()
    if normalized not in {"high", "medium", "low"}:
        return None
    return normalized.capitalize()


def _normalize_item_type(item_type: str) -> str | None:
    normalized = " ".join(item_type.split()).lower()
    if normalized not in {"story", "task", "chore", "bug"}:
        return None
    return normalized.capitalize()


def _normalize_size(size: str) -> str | None:
    normalized = " ".join(size.split()).upper()
    if normalized not in {"XS", "S", "M", "L", "XL"}:
        return None
    return normalized


def _normalize_validation_note(validation_note: str) -> str:
    """Return validation text without a duplicated leading 'Validation:' label."""

    normalized = validation_note.strip()
    if normalized.lower().startswith("validation:"):
        normalized = normalized.split(":", 1)[1].strip()
    return normalized


def _resolve_project_path(path: Path, project_root: Path | None = None) -> Path:
    root = project_root or PROJECT_ROOT
    if path.is_absolute():
        return path
    return root / path
