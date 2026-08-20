from __future__ import annotations

from pathlib import Path

import pytest

from ai_tech_lead.backlog_repository import (
    BacklogDraft,
    BacklogRefinementDraft,
    BacklogValidationError,
    MarkdownBacklogRepository,
    render_backlog_draft,
    render_backlog_refinement_draft,
    validate_backlog_draft,
    validate_backlog_refinement_draft,
)
from ai_tech_lead.backlog_status import (
    BacklogStatus,
    backlog_status_choices,
    normalize_backlog_status,
)


def test_repository_lists_gets_and_adds_markdown_items(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Existing item\n\nGoal:\nExisting goal\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    assert repository.next_item_id() == "ATL-002"

    draft = BacklogDraft(
        item_id="ATL-002",
        title="Improve backlog management",
        priority="High",
        approval_required=True,
        approval_reason="Changes backlog storage behaviour.",
        goal="Add safe backlog writing support.",
        constraints=["Require approval before writing", "Keep Markdown storage working"],
    )

    added_item = repository.add_item(draft)

    assert added_item.item_id == "ATL-002"
    assert added_item.title == "Improve backlog management"
    assert added_item.status == BacklogStatus.BACKLOG
    assert repository.get_item("ATL-002").body
    assert "Approval Required: yes" in backlog_path.read_text(encoding="utf-8")


def test_deferred_is_not_an_allowed_backlog_status() -> None:
    assert normalize_backlog_status("Deferred") is None
    assert "Deferred" not in backlog_status_choices()
    assert len(BacklogStatus) == 8


def test_repository_rejects_legacy_deferred_status(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Legacy item\n\n"
        "Status: Deferred\n\n"
        "Goal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    with pytest.raises(BacklogValidationError, match="Unknown backlog status 'Deferred'"):
        repository.list_items()


def test_list_open_items_excludes_done_items(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Active item\n\n"
        "Status: Backlog\n\n"
        "Goal:\nDo the thing.\n\n"
        "## ATL-002 - Finished item\n\n"
        "Status: Done\n\n"
        "Goal:\nDo the finished thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    open_items = repository.list_open_items()

    assert [item.item_id for item in open_items] == ["ATL-001"]
    assert open_items[0].status == BacklogStatus.BACKLOG


def test_list_open_items_sorted_orders_by_priority_and_simplicity(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-005 - High priority oldest simple\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-01\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the oldest simple thing.\n\n"
        "## ATL-002 - High priority newer simple\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-03-01\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the newer simple thing.\n\n"
        "## ATL-001 - High priority human review\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: High\n"
        "Created Date: 2024-02-01\n"
        "Approval Required: yes\n\n"
        "Goal:\nDo the reviewed thing.\n\n"
        "## ATL-003 - Medium priority item\n\n"
        "Status: Backlog\n"
        "Priority: Medium\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-15\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the medium thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    open_items = repository.list_open_items_sorted()

    assert [item.item_id for item in open_items] == ["ATL-005", "ATL-002", "ATL-001", "ATL-003"]
    assert open_items[0].priority == "High"
    assert open_items[0].complexity == "Low"
    assert open_items[0].created_date == "2024-01-01"


def test_validate_backlog_draft_rejects_missing_constraints() -> None:
    draft = BacklogDraft(
        item_id="ATL-010",
        title="Incomplete draft",
        priority="Low",
        approval_required=False,
        approval_reason="Documentation only.",
        goal="Do something.",
        constraints=[],
    )

    with pytest.raises(BacklogValidationError, match="constraint"):
        validate_backlog_draft(draft)


def test_validate_backlog_draft_rejects_missing_priority() -> None:
    draft = BacklogDraft(
        item_id="ATL-010",
        title="Incomplete draft",
        priority="Urgent",
        approval_required=False,
        approval_reason="Documentation only.",
        goal="Do something.",
        constraints=["Keep it small"],
    )

    with pytest.raises(BacklogValidationError, match="priority"):
        validate_backlog_draft(draft)


def test_validate_backlog_refinement_draft_rejects_missing_priority() -> None:
    draft = BacklogRefinementDraft(
        item_id="ATL-010",
        title="Refine backlog item creation",
        creator="Human",
        item_type="Story",
        epic="Backlog Management",
        priority="Urgent",
        size="M",
        approval_required=True,
        approval_reason="Touches backlog storage and research flow.",
        problem="Rob needs structured backlog refinement before coding starts.",
        desired_outcome="Turn rough ideas into refined backlog items.",
        scope=["Refine backlog ideas"],
        out_of_scope=["Build a full RAG system"],
        acceptance_criteria=["The item includes research cache usage"],
        duplicate_check_result="No duplicate found.",
        stale_check_result="No stale item found.",
        already_done_check_result="Not already done.",
        research_required=True,
        research_cache_used=["docs/research/backlog-refinement-implementation-patterns.md"],
        external_research_needed=False,
        recommended_implementation_pattern="Use schema-validated structured output.",
        patterns_explicitly_rejected=["Freeform prose"],
        freshness_risk="Low.",
        implementation_guidance="Check the cache first and keep the item structured.",
        approval_risk_flags=["Touches backlog storage"],
    )

    with pytest.raises(BacklogValidationError, match="priority"):
        validate_backlog_refinement_draft(draft)


def test_update_item_status_sets_new_status(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Some item\n\nStatus: Backlog\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    item = repository.update_item_status("ATL-001", " done ")

    assert item.item_id == "ATL-001"
    assert item.status == BacklogStatus.DONE
    text = backlog_path.read_text(encoding="utf-8")
    assert "Status: Done" in text
    assert "Status: Backlog" not in text


def test_update_item_status_inserts_when_missing(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Some item\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    repository.update_item_status("ATL-001", "In Progress")

    text = backlog_path.read_text(encoding="utf-8")
    assert "Status: In Progress" in text


def test_complete_item_normalizes_existing_validation_prefix(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Some item\n\nStatus: Backlog\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    item = repository.complete_item("ATL-001", "Validation: uv run pytest tests/test_one.py -q")

    assert item.status == BacklogStatus.DONE
    text = backlog_path.read_text(encoding="utf-8")
    assert "Status: Done" in text
    assert "Status: Backlog" not in text
    assert text.count("Validation:") == 1
    assert "Validation: uv run pytest tests/test_one.py -q" in text


# SQLite-backed backlog ownership (complete_item/update_item_status against a
# SqliteBacklogRepository) was replaced by the Google Sheets repository — see
# test_backlog_sheets_repository.py and test_backlog_runtime_store.py.


def test_update_item_status_accepts_wont_do(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Some item\n\nStatus: Backlog\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )

    repository = MarkdownBacklogRepository(backlog_path)
    item = repository.update_item_status("ATL-001", "won't do")

    assert item.status == BacklogStatus.WONT_DO
    assert "Status: Won't Do" in backlog_path.read_text(encoding="utf-8")


def test_update_item_status_rejects_unknown_status(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Some item\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    with pytest.raises(ValueError, match="1=Backlog, 2=Not Done, 3=In Progress"):
        repository.update_item_status("ATL-001", "Almost Done")


def test_update_item_status_raises_for_unknown_id(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Some item\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    with pytest.raises(ValueError, match="ATL-999"):
        repository.update_item_status("ATL-999", "Done")


def test_render_backlog_draft_is_markdown_backlog_format() -> None:
    draft = BacklogDraft(
        item_id="ATL-010",
        title="Add Telegram backlog proposals",
        priority="Medium",
        approval_required=True,
        approval_reason="Adds a backlog writing workflow.",
        goal="Allow approved backlog drafts to be appended.",
        constraints=["Do not write without approval"],
    )

    rendered = render_backlog_draft(draft)

    assert rendered.startswith("## ATL-010 - Add Telegram backlog proposals")
    assert "Status: Backlog" in rendered
    assert "Priority: Medium" in rendered
    assert "Approval Required: yes" in rendered
    assert "- Do not write without approval" in rendered


def test_render_backlog_refinement_draft_includes_research_context() -> None:
    draft = BacklogRefinementDraft(
        item_id="ATL-010",
        title="Refine backlog item creation",
        creator="Human",
        item_type="Story",
        epic="Backlog Management",
        priority="High",
        size="M",
        approval_required=True,
        approval_reason="Touches backlog storage and research flow.",
        problem="Rob needs structured backlog refinement before coding starts.",
        desired_outcome="Turn rough ideas into refined backlog items.",
        scope=["Refine backlog ideas", "Capture research outcome"],
        out_of_scope=["Build a full RAG system"],
        acceptance_criteria=[
            "The item includes research cache usage",
            "The item lists rejected patterns",
        ],
        duplicate_check_result="No duplicate found.",
        stale_check_result="No stale item found.",
        already_done_check_result="Not already done.",
        research_required=True,
        research_cache_used=["docs/research/backlog-refinement-implementation-patterns.md"],
        external_research_needed=False,
        recommended_implementation_pattern="Use schema-validated structured output.",
        patterns_explicitly_rejected=["Freeform prose", "Heuristic duplicate matching"],
        freshness_risk="Low.",
        implementation_guidance="Check the cache first and keep the item structured.",
        approval_risk_flags=["Touches backlog storage"],
    )

    validate_backlog_refinement_draft(draft)
    rendered = render_backlog_refinement_draft(draft)

    assert rendered.startswith("## ATL-010 - Refine backlog item creation")
    assert "Creator: Human" in rendered
    assert "Type: Story" in rendered
    assert "Epic: Backlog Management" in rendered
    assert "Priority: High" in rendered
    assert "Size: M" in rendered
    assert "Research Required: yes" in rendered
    assert "Research Cache Used:" in rendered
    assert "- docs/research/backlog-refinement-implementation-patterns.md" in rendered
    assert "Patterns Explicitly Rejected:" in rendered
    assert "Approval / Risk Flags:" in rendered


def test_add_refined_item_appends_rendered_backlog_item(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Existing item\n\nStatus: Backlog\n\nGoal:\nExisting goal\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    draft = BacklogRefinementDraft(
        item_id="ATL-002",
        title="Refine backlog item creation",
        creator="Human",
        item_type="Story",
        epic="Backlog Management",
        priority="High",
        size="M",
        approval_required=True,
        approval_reason="Touches backlog storage and research flow.",
        problem="Rob needs structured backlog refinement before coding starts.",
        desired_outcome="Turn rough ideas into refined backlog items.",
        scope=["Refine backlog ideas", "Capture research outcome"],
        out_of_scope=["Build a full RAG system"],
        acceptance_criteria=["The item includes research cache usage"],
        duplicate_check_result="No duplicate found.",
        stale_check_result="No stale item found.",
        already_done_check_result="Not already done.",
        research_required=True,
        research_cache_used=["docs/research/backlog-refinement-implementation-patterns.md"],
        external_research_needed=False,
        recommended_implementation_pattern="Use schema-validated structured output.",
        patterns_explicitly_rejected=["Freeform prose"],
        freshness_risk="Low.",
        implementation_guidance="Check the cache first and keep the item structured.",
        approval_risk_flags=["Touches backlog storage"],
    )

    item = repository.add_refined_item(draft)

    assert item.item_id == "ATL-002"
    text = backlog_path.read_text(encoding="utf-8")
    assert "Problem:" in text
    assert "Research Required: yes" in text
