from __future__ import annotations

import logging
from dataclasses import replace

from langgraph.checkpoint.memory import MemorySaver

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_workflow_graph import (
    NodeName,
    build_graph,
    route_after_approval,
    route_after_check_research,
    route_after_formulate_task,
    route_after_review_plan,
    run_coding_agent_node,
)

from helpers import valid_settings_dict


def graph_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "request": "Backlog item: JH-001\nTitle: Local placeholder",
        "brief": "",
        "force_approval": False,
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
        "approval_reason": "Safe local work.",
        "approved": False,
        "formulated_task": "",
        "plan_text": "",
        "plan_approved": False,
        "plan_review_reason": "",
        "plan_correction": "",
        "plan_rejection_count": 0,
        "plan_needs_human_review": False,
        "agent_instruction": "",
        "coding_agent_result": "",
    }
    state.update(overrides)
    return state


def test_graph_state_defaults_do_not_require_human_input() -> None:
    state = graph_state()

    assert state["orchestrator_input_required"] is False
    assert state["orchestrator_input_kind"] == ""
    assert state["orchestrator_input_reason"] == ""
    assert state["orchestrator_input_question"] == ""
    assert state["orchestrator_input_source_node"] == ""
    assert state["task_feedback"] == []


def test_graph_state_can_store_orchestrator_input_request_fields() -> None:
    state = graph_state(
        orchestrator_input_required=True,
        orchestrator_input_kind="clarification",
        orchestrator_input_reason="The request is ambiguous.",
        orchestrator_input_question="Should I keep the existing structure?",
        orchestrator_input_source_node="2_review_risk",
    )

    assert state["orchestrator_input_required"] is True
    assert state["orchestrator_input_kind"] == "clarification"
    assert state["orchestrator_input_reason"] == "The request is ambiguous."
    assert state["orchestrator_input_question"] == "Should I keep the existing structure?"
    assert state["orchestrator_input_source_node"] == "2_review_risk"


def test_graph_state_can_store_task_feedback_list() -> None:
    state = graph_state(
        task_feedback=[
            "Please keep this change small.",
            "Do not update Telegram yet.",
        ]
    )

    assert state["task_feedback"] == [
        "Please keep this change small.",
        "Do not update Telegram yet.",
    ]


def test_routes_follow_explicit_approval_state() -> None:
    assert route_after_formulate_task(graph_state(needs_approval=True)) == NodeName.APPROVAL_REQUIRED
    assert route_after_formulate_task(graph_state(needs_approval=False)) == NodeName.REQUEST_PLAN
    assert route_after_approval(graph_state(approved=True)) == NodeName.REQUEST_PLAN
    assert route_after_approval(graph_state(approved=False)) == NodeName.END_NODE


def test_route_after_check_research_simple_task_skips_gate() -> None:
    state = graph_state(research_evidence_required=False, online_research_approved=False)
    assert route_after_check_research(state) == NodeName.REVIEW_RISK


def test_route_after_check_research_complex_with_sufficient_sources_skips_gate() -> None:
    state = graph_state(
        research_evidence_required=True,
        online_research_approved=True,
        research_sources_found=2,
    )
    assert route_after_check_research(state) == NodeName.REVIEW_RISK


def test_route_after_check_research_complex_with_insufficient_sources_gates() -> None:
    state = graph_state(
        research_evidence_required=True,
        online_research_approved=False,
        research_sources_found=1,
    )
    assert route_after_check_research(state) == NodeName.RESEARCH_GATE


def test_run_coding_agent_node_override_can_disable_execution(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    seen_execute_values: list[bool] = []

    def fake_load_settings():
        return settings

    def fake_run_coding_agent(
        agent_instruction,
        project_root,
        settings,
        progress_callback=None,
    ):
        seen_execute_values.append(settings.execute_coding_agent)

        class Result:
            message = "disabled"
            returncode = None
            duration_seconds = 0.0
            changed_files_delta: tuple[str, ...] = ()
            command: list[str] = []

            def summary(self) -> str:
                return "disabled"

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent)

    state = run_coding_agent_node(
        graph_state(agent_instruction="Do the task"),
        execute_coding_agent_override=False,
    )

    assert seen_execute_values == [False]
    assert state["coding_agent_result"] == "disabled"


def test_run_coding_agent_node_forwards_progress_updates(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    progress_messages: list[str] = []

    def fake_load_settings():
        return settings

    def fake_run_coding_agent(
        agent_instruction,
        project_root,
        settings,
        progress_callback=None,
    ):
        assert callable(progress_callback)
        progress_callback("Coding agent still running (about 1m 0s elapsed).")

        class Result:
            message = "done"
            returncode = 0
            duration_seconds = 1.0
            changed_files_delta: tuple[str, ...] = ()
            command: list[str] = ["codex"]

            def summary(self) -> str:
                return "done"

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent)

    state = run_coding_agent_node(
        graph_state(agent_instruction="Do the task"),
        execute_coding_agent_override=True,
        progress_callback=progress_messages.append,
    )

    assert progress_messages == ["Coding agent still running (about 1m 0s elapsed)."]
    assert state["coding_agent_result"] == "done"


def test_graph_runs_to_disabled_coding_agent_result(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    def fake_load_settings():
        return settings

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.clarification_checker.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.research_checker.load_research_cache_entries", lambda **_kw: [])
    app = build_graph(execute_coding_agent_override=False)

    result = app.invoke(graph_state())

    assert "Code runs successfully" in result["brief"]
    assert result["needs_approval"] is True
    assert "AI risk review is off, so I need your approval before continuing." in result["approval_reason"]
    assert result["approved"] is False
    assert result["agent_instruction"] == ""
    assert result["coding_agent_result"] == ""


def test_run_coding_agent_node_logs_approval_context(monkeypatch, caplog) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=False)

    class _DisabledResult:
        message = "Coding agent execution disabled by settings."
        returncode = None
        duration_seconds = 0.0
        changed_files_delta: tuple[str, ...] = ()
        command: list[str] = []

        def summary(self) -> str:
            return self.message

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", lambda **_kw: _DisabledResult()
    )

    caplog.set_level(logging.INFO)
    run_coding_agent_node(
        graph_state(needs_approval=True, approved=True, approval_reason="Human said yes."),
        execute_coding_agent_override=False,
    )

    assert "approval_source=human_approved" in caplog.text
    assert "approval_required=True" in caplog.text
    assert "approved=True" in caplog.text
    assert "configured coding-agent backend" in caplog.text
    assert "Codex" not in caplog.text


def test_rejected_graph_resumes_without_coding_agent_result_index_error(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    def fake_load_settings():
        return settings

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.clarification_checker.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.research_checker.load_research_cache_entries", lambda **_kw: [])

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-reject-path"}}

    app.invoke(graph_state(), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.APPROVAL_REQUIRED,)

    app.update_state(thread_config, {"approved": False})
    app.invoke(None, config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approved"] is False
    assert final_state.values["coding_agent_result"] == ""


def test_route_after_review_plan_approved() -> None:
    state = graph_state(plan_approved=True)
    assert route_after_review_plan(state) == NodeName.CREATE_AGENT_INSTRUCTION


def test_route_after_review_plan_rejected_first_time() -> None:
    state = graph_state(plan_approved=False, plan_rejection_count=1)
    assert route_after_review_plan(state) == NodeName.REQUEST_PLAN


def test_route_after_review_plan_rejected_twice_goes_to_human() -> None:
    state = graph_state(plan_approved=False, plan_rejection_count=2)
    assert route_after_review_plan(state) == NodeName.PLAN_HUMAN_GATE


def test_route_after_review_plan_reviewer_unavailable_goes_to_human() -> None:
    state = graph_state(plan_approved=False, plan_needs_human_review=True)
    assert route_after_review_plan(state) == NodeName.PLAN_HUMAN_GATE
