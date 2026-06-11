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
        priority="High",
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
        priority="Urgent",
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
        "# Backlog\n\n"
        "## ATL-001 - Some item\n\n"
        "Status: Backlog\n\n"
        "Goal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    item = repository.update_item_status("ATL-001", "Done")

    assert item.item_id == "ATL-001"
    text = backlog_path.read_text(encoding="utf-8")
    assert "Status: Done" in text
    assert "Status: Backlog" not in text


def test_update_item_status_inserts_when_missing(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Some item\n\n"
        "Goal:\nDo the thing.\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    repository.update_item_status("ATL-001", "In Progress")

    text = backlog_path.read_text(encoding="utf-8")
    assert "Status: In Progress" in text


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
    assert "Priority: Medium" in rendered
    assert "Approval Required: yes" in rendered
    assert "- Do not write without approval" in rendered


def test_render_backlog_refinement_draft_includes_research_context() -> None:
    draft = BacklogRefinementDraft(
        item_id="ATL-010",
        title="Refine backlog item creation",
        priority="High",
        approval_required=True,
        approval_reason="Touches backlog storage and research flow.",
        problem="Rob needs structured backlog refinement before coding starts.",
        desired_outcome="Turn rough ideas into refined backlog items.",
        scope=["Refine backlog ideas", "Capture research outcome"],
        out_of_scope=["Build a full RAG system"],
        acceptance_criteria=["The item includes research cache usage", "The item lists rejected patterns"],
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
    assert "Priority: High" in rendered
    assert "Research Required: yes" in rendered
    assert "Research Cache Used:" in rendered
    assert "- docs/research/backlog-refinement-implementation-patterns.md" in rendered
    assert "Patterns Explicitly Rejected:" in rendered
    assert "Approval / Risk Flags:" in rendered


def test_add_refined_item_appends_rendered_backlog_item(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Existing item\n\n"
        "Status: Backlog\n\n"
        "Goal:\nExisting goal\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)

    draft = BacklogRefinementDraft(
        item_id="ATL-002",
        title="Refine backlog item creation",
        priority="High",
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
