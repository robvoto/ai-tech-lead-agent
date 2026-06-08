"""Backlog loading helpers for the local AI Technical Lead prototype.

Purpose:
- Read backlog tasks from the configured backlog path.
- Convert one selected backlog item into LangGraph state.

Important design rule:
- This module must NOT silently choose work in a hidden or magical way.
- The caller should normally choose an item by ID, for example "JH-001".
- Risk/approval is reviewed by the graph, not decided by the backlog file.
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

    body:
        The markdown content under the heading until the next backlog item.
    """

    item_id: str
    title: str
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
        "needs_approval": False,
        "approval_reason": "Risk review has not run yet.",
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

    return BacklogItem(
        item_id=item_id.strip(),
        title=title.strip(),
        body=body,
    )


def _resolve_backlog_path(backlog_path: Path | None) -> Path:
    """Resolve an explicit or configured backlog path."""

    if backlog_path is not None:
        return backlog_path

    configured_path = Path(load_settings().backlog_path)    
    if configured_path.is_absolute():
        return configured_path

    return PROJECT_ROOT / configured_path
