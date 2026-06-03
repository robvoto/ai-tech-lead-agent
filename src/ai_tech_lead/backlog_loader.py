"""Backlog loading helpers for the local AI Technical Lead prototype.

Purpose:
- Read backlog tasks from the configured backlog path.
- Convert one selected backlog item into LangGraph state.

Important design rule:
- This module must NOT silently choose work in a hidden or magical way.
- The caller should normally choose an item by ID, for example "JH-001".
- Approval must be explicit in the backlog item while the real LLM risk-review
  node is still a TODO.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_tech_lead.app_settings import load_settings
from ai_tech_lead.coding_workflow_graph import GraphState
from ai_tech_lead.config import PROJECT_ROOT


@dataclass(frozen=True)
class BacklogItem:
    """One parsed backlog item.

    item_id:
        The backlog identifier, such as "JH-001" or "ATL-001".

    title:
        The short human-readable title from the markdown heading.

    approval_required:
        Explicit human-owned approval flag from the backlog item. This avoids
        fragile keyword heuristics until a proper LLM risk-review node exists.

    approval_reason:
        Human-readable reason included in logs and the agent instruction.

    body:
        The markdown content under the heading until the next backlog item.
    """

    item_id: str
    title: str
    approval_required: bool
    approval_reason: str
    body: str


def load_backlog_items(backlog_path: Path | None = None) -> list[BacklogItem]:
    """Parse all backlog items from the markdown backlog file.

    This function only reads and parses the backlog.
    It does not decide which task should run.
    """

    resolved_backlog_path = _resolve_backlog_path(backlog_path)

    if not resolved_backlog_path.exists():
        raise FileNotFoundError(f"Backlog file not found: {resolved_backlog_path}")

    text = resolved_backlog_path.read_text(encoding="utf-8")
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
        raise ValueError(f"No backlog items found in {resolved_backlog_path}")

    return items


def load_backlog_item_by_id(item_id: str, backlog_path: Path | None = None) -> BacklogItem:
    """Load one backlog item by explicit ID.

    Use this for normal prototype execution.
    It avoids silently picking the wrong backlog item.
    """

    requested_id = item_id.strip().lower()

    for item in load_backlog_items(backlog_path):
        if item.item_id.lower() == requested_id:
            return item

    available_ids = ", ".join(item.item_id for item in load_backlog_items(backlog_path))
    raise ValueError(
        f"Backlog item '{item_id}' was not found. Available IDs: {available_ids}"
    )


def load_first_backlog_item_for_demo(backlog_path: Path | None = None) -> BacklogItem:
    """Load the first backlog item for demo use only.

    This is intentionally named "for_demo" so it is obvious that this is not
    the final production selection rule.
    """

    return load_backlog_items(backlog_path)[0]


def backlog_item_to_graph_state(item: BacklogItem) -> GraphState:
    """Convert one backlog item into initial LangGraph state."""

    request = f"""
Backlog item: {item.item_id}
Title: {item.title}

{item.body}
""".strip()

    return {
        "request": request,
        "brief": "",
        "needs_approval": item.approval_required,
        "approval_reason": item.approval_reason,
        "approved": False,
        "agent_instruction": "",
        "coding_agent_result": "",
    }


def _build_item(heading: str, body_lines: list[str]) -> BacklogItem:
    """Build a BacklogItem from one markdown heading and its body."""

    if " - " in heading:
        item_id, title = heading.split(" - ", 1)
    else:
        item_id = "UNKNOWN"
        title = heading

    body = "\n".join(body_lines).strip()
    approval_required = _parse_required_approval(item_id.strip(), body)
    approval_reason = _parse_optional_field(
        body,
        "Approval Reason",
        default="Approval requirement supplied by backlog item.",
    )

    return BacklogItem(
        item_id=item_id.strip(),
        title=title.strip(),
        approval_required=approval_required,
        approval_reason=approval_reason,
        body=body,
    )


def _parse_required_approval(item_id: str, body: str) -> bool:
    """Read the required explicit approval flag from a backlog item body."""

    raw_value = _parse_optional_field(body, "Approval Required")
    if raw_value is None:
        raise ValueError(
            f"Backlog item '{item_id}' must include 'Approval Required: yes' "
            "or 'Approval Required: no'."
        )

    normalized_value = raw_value.strip().lower()
    if normalized_value in {"yes", "y", "true"}:
        return True
    if normalized_value in {"no", "n", "false"}:
        return False

    raise ValueError(
        f"Backlog item '{item_id}' has invalid Approval Required value "
        f"'{raw_value}'. Use yes or no."
    )


def _parse_optional_field(body: str, field_name: str, default: str | None = None) -> str | None:
    """Parse one simple 'Field Name: value' line from a markdown backlog item."""

    prefix = f"{field_name}:"
    for line in body.splitlines():
        if line.strip().lower().startswith(prefix.lower()):
            value = line.split(":", 1)[1].strip()
            return value or default

    return default


def _resolve_backlog_path(backlog_path: Path | None) -> Path:
    """Resolve an explicit or configured backlog path."""

    if backlog_path is not None:
        return backlog_path

    configured_path = Path(load_settings().backlog_path)
    if configured_path.is_absolute():
        return configured_path

    return PROJECT_ROOT / configured_path
