from __future__ import annotations

from pathlib import Path

from ai_tech_lead.backlog_loader import (
    backlog_item_to_graph_state,
    load_backlog_item_by_id,
    load_backlog_items,
)


def test_load_backlog_items_parses_explicit_approval(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        """
# Backlog

## JH-001 - Local placeholder

Approval Required: no
Approval Reason: Safe local work.

Goal:
Create a placeholder.

## ATL-002 - Approval task

Approval Required: yes
Approval Reason: Changes approval behavior.
""".strip(),
        encoding="utf-8",
    )

    items = load_backlog_items(backlog_path)

    assert [item.item_id for item in items] == ["JH-001", "ATL-002"]
    assert items[0].title == "Local placeholder"
    assert "Create a placeholder." in items[0].body
    assert items[1].title == "Approval task"
    assert "Approval Reason: Changes approval behavior." in items[1].body


def test_load_backlog_item_by_id_is_case_insensitive(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        """
## JH-001 - Local placeholder

Approval Required: no
Approval Reason: Safe local work.
""".strip(),
        encoding="utf-8",
    )

    item = load_backlog_item_by_id("jh-001", backlog_path)

    assert item.title == "Local placeholder"


def test_backlog_item_to_graph_state_sets_required_initial_fields(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        """
## JH-001 - Local placeholder

Approval Required: no
Approval Reason: Safe local work.
""".strip(),
        encoding="utf-8",
    )
    item = load_backlog_item_by_id("JH-001", backlog_path)

    state = backlog_item_to_graph_state(item)

    assert "Backlog item: JH-001" in state["request"]
    assert state["needs_approval"] is False
    assert state["approval_reason"] == "Risk review has not run yet."
    assert state["approved"] is False
    assert state["agent_instruction"] == ""
    assert state["coding_agent_result"] == ""
