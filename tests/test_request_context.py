from ai_tech_lead.request_context import resolve_request_context, understand_request


def test_unresolved_reference_is_not_guessed():
    result = resolve_request_context("Code AF-052 for Agent Factory")

    assert result["unresolved_references"] == ["AF-052"]
    assert result["clarification_question"] == (
        "What does AF-052 refer to, and where should I retrieve it from?"
    )


def test_fetched_backlog_reference_resolves_and_bounds_request():
    result = resolve_request_context(
        "Code AF-052 for Agent Factory",
        supplied_context={
            "project_reference": {"project_key": "agent-factory"},
            "backlog_reference": {
                "item_id": "AF-052",
                "title": "Add bounded package validation",
                "body": "Acceptance Criteria: validation stops safely on unknown package state.",
            },
        },
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
