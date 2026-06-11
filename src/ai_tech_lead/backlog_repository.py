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
    interrupt_before_implementation: bool = False


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
    priority: str
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

    def update_item_status(self, item_id: str, new_status: str) -> BacklogItem:
        """Set or replace the Status line of an existing backlog item."""
        normalized_id = item_id.strip().upper()
        self.get_item(normalized_id)  # raises ValueError if not found

        new_status = new_status.strip()
        if not new_status:
            raise ValueError("Status cannot be empty.")

        text = self._backlog_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)

        heading_re = re.compile(
            rf"^## {re.escape(normalized_id)} - ", re.IGNORECASE
        )
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
            lines[status_line_index] = f"Status: {new_status}\n"
        else:
            # Insert status line immediately after the heading
            lines.insert(item_start + 1, f"Status: {new_status}\n")

        self._backlog_path.write_text("".join(lines), encoding="utf-8")
        return self.get_item(normalized_id)

    def add_refined_item(self, draft: BacklogRefinementDraft) -> BacklogItem:
        validate_backlog_refinement_draft(
            draft,
            existing_ids={item.item_id for item in self.list_items()},
        )
        rendered_item = render_backlog_refinement_draft(draft)
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
    if _normalize_priority(draft.priority) is None:
        raise BacklogValidationError("Backlog priority must be High, Medium, or Low.")
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
    _require_non_empty_items(draft.research_cache_used, "Research cache used")
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
    priority_text = _normalize_priority(draft.priority)
    research_required_text = "yes" if draft.research_required else "no"
    external_research_needed_text = "yes" if draft.external_research_needed else "no"
    approval_risk_flags = _format_list_section(
        draft.approval_risk_flags,
        none_text="None",
    )
    patterns_rejected = _format_list_section(draft.patterns_explicitly_rejected)
    research_cache_used = _format_list_section(draft.research_cache_used)
    scope = _format_list_section(draft.scope)
    out_of_scope = _format_list_section(draft.out_of_scope)
    acceptance_criteria = _format_list_section(draft.acceptance_criteria)
    return "\n".join(
        [
            f"## {draft.item_id} - {draft.title.strip()}",
            "",
            "Status: Backlog",
            f"Priority: {priority_text}",
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


def _build_item(heading: str, body_lines: list[str]) -> BacklogItem:
    if " - " in heading:
        item_id, title = heading.split(" - ", 1)
    else:
        item_id = "UNKNOWN"
        title = heading

    body = "\n".join(body_lines).strip()
    interrupt_before_implementation = _parse_yes_no_field(
        body_lines, "Interrupt Before Implementation"
    )
    return BacklogItem(
        item_id=item_id.strip(),
        title=title.strip(),
        body=body,
        interrupt_before_implementation=interrupt_before_implementation,
    )


def _parse_yes_no_field(body_lines: list[str], field_name: str) -> bool:
    prefix = f"{field_name}:"
    for line in body_lines:
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            value = stripped[len(prefix):].strip().lower()
            return value == "yes"
    return False


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


def _resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
