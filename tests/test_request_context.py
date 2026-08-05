from ai_tech_lead.request_context import resolve_request_context, understand_request
from ai_tech_lead.target_project_context import BacklogItemContext, TargetProjectContext


def test_unresolved_reference_is_not_guessed():
    result = resolve_request_context("Code AF-052 for Agent Factory")

    assert result["unresolved_references"] == ["AF-052"]
    assert result["clarification_question"] == (
        "What does AF-052 refer to, and where should I retrieve it from?"
    )


def test_fetched_backlog_reference_resolves_and_bounds_request():
    result = resolve_request_context(
        "Code AF-052 for Agent Factory",
        target_project_context=TargetProjectContext(
            project_key="agent-factory",
            backlog_item=BacklogItemContext(
                project_key="agent-factory",
                spreadsheet_id="spreadsheet-a",
                sheet_name="Backlog",
                item_id="AF-052",
                title="Add bounded package validation",
                body="Acceptance Criteria: validation stops safely on unknown package state.",
            ),
        ),
    )

    assert result["unresolved_references"] == []
    assert result["resolved_project_identity"] == "agent-factory"
    assert "Add bounded package validation" in result["bounded_request"]
    assert "Acceptance Criteria" in result["bounded_request"]


def test_report_only_does_not_request_execution():
    result = understand_request("Review AF-052 and report only; do not code")

    assert result.intent == "review"
    assert result.execution_requested is False


def test_plain_request_requires_no_backlog_context():
    result = resolve_request_context("Review the current architecture for duplication")

    assert result["unresolved_references"] == []
    assert result["clarification_question"] == ""


def test_clarification_answer_resolves_only_the_asked_reference():
    result = resolve_request_context(
        "Code AF-052 for Agent Factory",
        clarification_answer="AF-052 is the agent-factory repo, root at /projects/agent-factory.",
    )

    assert result["unresolved_references"] == []
    assert result["clarification_question"] == ""
    assert any("AF-052 clarified by human" in note for note in result["context_resolution_evidence"])
    assert "Clarification for AF-052" in result["bounded_request"]


def test_empty_clarification_answer_leaves_reference_unresolved():
    result = resolve_request_context(
        "Code AF-052 for Agent Factory",
        clarification_answer="   ",
    )

    assert result["unresolved_references"] == ["AF-052"]
    assert result["clarification_question"] != ""
