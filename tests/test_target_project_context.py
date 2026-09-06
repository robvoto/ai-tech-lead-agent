"""Tests for BacklogItemContext's priority/size round-trip (ATL-034)."""

from __future__ import annotations

from ai_tech_lead.target_project_context import BacklogItemContext


def test_backlog_item_context_round_trips_priority_and_size() -> None:
    context = BacklogItemContext(
        project_key="ai-tech-lead",
        spreadsheet_id="spreadsheet-a",
        sheet_name="Backlog",
        item_id="ATL-034",
        title="Add coding-agent tiers",
        priority="High",
        size="M",
    )

    payload = context.to_payload()
    assert payload["priority"] == "High"
    assert payload["size"] == "M"

    restored = BacklogItemContext.from_payload(payload)
    assert restored.priority == "High"
    assert restored.size == "M"


def test_backlog_item_context_omits_empty_priority_and_size_from_payload() -> None:
    context = BacklogItemContext(
        project_key="ai-tech-lead",
        spreadsheet_id="spreadsheet-a",
        sheet_name="Backlog",
        item_id="ATL-034",
    )

    payload = context.to_payload()

    assert "priority" not in payload
    assert "size" not in payload
