"""Backlog loading helpers for the local AI Technical Lead prototype.

Purpose:
- Read backlog tasks from the configured backlog path.
- Convert one selected backlog item into LangGraph state.

Important design rule:
- This module must NOT silently choose work in a hidden or magical way.
- The caller should normally choose an item by ID, for example "ATL-001".
- Risk/approval is reviewed by the graph, not decided by the backlog file.
"""

from __future__ import annotations

from pathlib import Path

from ai_tech_lead.app_settings import load_settings
from ai_tech_lead.backlog_repository import BacklogItem, MarkdownBacklogRepository
from ai_tech_lead.coding_workflow_graph import GraphState


def load_backlog_items(backlog_path: Path | None = None) -> list[BacklogItem]:
    """Parse all backlog items from the configured backlog repository."""

    return _repository(backlog_path).list_items()


def load_backlog_item_by_id(item_id: str, backlog_path: Path | None = None) -> BacklogItem:
    """Load one backlog item by explicit ID.

    Use this for normal prototype execution.
    It avoids silently picking the wrong backlog item.
    """

    return _repository(backlog_path).get_item(item_id)


def load_first_backlog_item_for_demo(backlog_path: Path | None = None) -> BacklogItem:
    """Load the first backlog item for demo use only."""

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


def _repository(backlog_path: Path | None) -> MarkdownBacklogRepository:
    if backlog_path is not None:
        return MarkdownBacklogRepository(backlog_path)
    return MarkdownBacklogRepository(Path(load_settings().backlog_path))
