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
from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository, repository_from_settings
from ai_tech_lead.coding_workflow_graph import GraphState, build_initial_graph_state


def load_backlog_items(backlog_path: Path | None = None) -> list[BacklogItem]:
    """Parse all backlog items from the configured backlog repository."""

    return _repository(backlog_path).list_items()


def load_open_backlog_items(backlog_path: Path | None = None) -> list[BacklogItem]:
    """Parse only backlog items that are still eligible for work."""

    return _repository(backlog_path).list_open_items()


def load_backlog_item_by_id(item_id: str, backlog_path: Path | None = None) -> BacklogItem:
    """Load one backlog item by explicit ID.

    Use this for normal prototype execution.
    It avoids silently picking the wrong backlog item.
    """

    return _repository(backlog_path).get_item_for_execution(item_id)


def load_first_backlog_item_for_demo(backlog_path: Path | None = None) -> BacklogItem:
    """Load the first backlog item for demo use only."""

    items = load_open_backlog_items(backlog_path)
    if not items:
        raise ValueError("No open backlog items found for demo selection.")
    return items[0]


def backlog_item_to_graph_state(item: BacklogItem) -> GraphState:
    """Convert one backlog item into initial LangGraph state."""

    request = f"""
Backlog item: {item.item_id}
Title: {item.title}

{item.body}
""".strip()

    return build_initial_graph_state(
        request,
        force_approval=item.interrupt_before_implementation,
    )


def _repository(backlog_path: Path | None) -> MarkdownBacklogRepository | SheetsBacklogRepository:
    """Return the live backlog repository.

    An explicit backlog_path selects the historical Markdown format. The
    default (no path) is the canonical Google Sheets backlog, resolved from
    this process's own configured project (see docs/INDEX.md).
    """

    if backlog_path is not None:
        resolved = (
            backlog_path
            if backlog_path.is_absolute()
            else (Path(load_settings().project_root) / backlog_path)
        )
        return MarkdownBacklogRepository(resolved)

    return repository_from_settings(load_settings())
