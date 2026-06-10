from __future__ import annotations

from pathlib import Path

import pytest

from ai_tech_lead.backlog_repository import (
    BacklogDraft,
    BacklogValidationError,
    MarkdownBacklogRepository,
    render_backlog_draft,
    validate_backlog_draft,
)


def test_repository_lists_gets_and_adds_markdown_items(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Existing item\n\n"
        "Goal:\nExisting goal\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    assert repository.next_item_id() == "ATL-002"

    draft = BacklogDraft(
        item_id="ATL-002",
        title="Improve backlog management",
        approval_required=True,
        approval_reason="Changes backlog storage behaviour.",
        goal="Add safe backlog writing support.",
        constraints=["Require approval before writing", "Keep Markdown storage working"],
    )

    added_item = repository.add_item(draft)

    assert added_item.item_id == "ATL-002"
    assert added_item.title == "Improve backlog management"
    assert repository.get_item("ATL-002").body
    assert "Approval Required: yes" in backlog_path.read_text(encoding="utf-8")


def test_validate_backlog_draft_rejects_missing_constraints() -> None:
    draft = BacklogDraft(
        item_id="ATL-010",
        title="Incomplete draft",
        approval_required=False,
        approval_reason="Documentation only.",
        goal="Do something.",
        constraints=[],
    )

    with pytest.raises(BacklogValidationError, match="constraint"):
        validate_backlog_draft(draft)


def test_render_backlog_draft_is_markdown_backlog_format() -> None:
    draft = BacklogDraft(
        item_id="ATL-010",
        title="Add Telegram backlog proposals",
        approval_required=True,
        approval_reason="Adds a backlog writing workflow.",
        goal="Allow approved backlog drafts to be appended.",
        constraints=["Do not write without approval"],
    )

    rendered = render_backlog_draft(draft)

    assert rendered.startswith("## ATL-010 - Add Telegram backlog proposals")
    assert "Approval Required: yes" in rendered
    assert "- Do not write without approval" in rendered
