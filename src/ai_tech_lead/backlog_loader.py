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

    return {
        "request": request,
        "brief": "",
        "force_approval": item.interrupt_before_implementation,
        "research_evidence_required": False,
        "research_sources_found": 0,
        "research_source_titles": [],
        "online_research_approved": False,
        "orchestrator_input_required": False,
        "orchestrator_input_kind": "",
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
        "orchestrator_input_source_node": "",
        "task_feedback": [],
        "needs_approval": False,
        "approval_reason": "Risk review has not run yet.",
        "approved": False,
        "approved_by": "",
        "formulated_task": "",
        "plan_text": "",
        "plan_approved": False,
        "plan_review_reason": "",
        "plan_correction": "",
        "plan_rejection_count": 0,
        "plan_needs_human_review": False,
        "agent_instruction": "",
        "coding_agent_result": "",
        "coding_agent_success": False,
        "coding_agent_changed_files": (),
        "coding_agent_command": "",
        "coding_agent_returncode": None,
        "coding_agent_timed_out": False,
        "coding_agent_performed_by": "",
    }


def _repository(backlog_path: Path | None) -> MarkdownBacklogRepository:
    if backlog_path is not None:
        if backlog_path.is_absolute():
            return MarkdownBacklogRepository(backlog_path)
        settings = load_settings()
        return MarkdownBacklogRepository(
            backlog_path,
            project_root=Path(settings.project_root),
        )
    settings = load_settings()
    return MarkdownBacklogRepository(
        Path(settings.backlog_path),
        project_root=Path(settings.project_root),
    )
