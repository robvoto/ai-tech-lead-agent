from __future__ import annotations

import logging
from dataclasses import replace

from helpers import valid_settings_dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_workflow_graph import (
    NodeName,
    build_graph,
    collect_research_evidence_node,
    request_plan_node,
    route_after_approval,
    route_after_check_research,
    route_after_research_interrupt,
    route_after_review_plan,
    route_after_tech_lead_analyse,
    run_coding_agent_node,
)
from ai_tech_lead.plan_reviewer import PlanReviewDecision


def graph_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "request": "Backlog item: JH-001\nTitle: Local placeholder",
        "brief": "",
        "force_approval": False,
        "research_evidence_required": False,
        "research_sources_found": 0,
        "research_source_titles": [],
        "research_source_locations": [],
        "research_source_summaries": [],
        "research_online_sources_found": 0,
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
        "coding_agent_retry_count": 0,
        "coding_agent_correction": "",
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
    assert state["research_source_titles"] == []
    assert state["research_source_locations"] == []
    assert state["research_source_summaries"] == []
    assert state["research_online_sources_found"] == 0
    assert state["coding_agent_retry_count"] == 0
    assert state["coding_agent_correction"] == ""


def test_graph_state_can_store_orchestrator_input_request_fields() -> None:
    state = graph_state(
        orchestrator_input_required=True,
        orchestrator_input_kind="research_approval",
        orchestrator_input_reason="Complex task needs external docs.",
        orchestrator_input_question="Fetch approved LangChain docs?",
        orchestrator_input_source_node="1b_check_research",
    )

    assert state["orchestrator_input_required"] is True
    assert state["orchestrator_input_kind"] == "research_approval"
    assert state["orchestrator_input_reason"] == "Complex task needs external docs."
    assert state["orchestrator_input_question"] == "Fetch approved LangChain docs?"
    assert state["orchestrator_input_source_node"] == "1b_check_research"


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
    assert (
        route_after_tech_lead_analyse(graph_state(needs_approval=True))
        == NodeName.APPROVAL_INTERRUPT
    )
    assert (
        route_after_tech_lead_analyse(graph_state(needs_approval=True, approved=True))
        == NodeName.REQUEST_PLAN
    )
    assert route_after_tech_lead_analyse(graph_state(needs_approval=False)) == NodeName.REQUEST_PLAN
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
    assert route_after_check_research(state) == NodeName.RESEARCH_INTERRUPT


def test_route_after_research_interrupt_rejection_ends_workflow() -> None:
    state = graph_state(online_research_approved=False)
    assert route_after_research_interrupt(state) == NodeName.END_NODE


def test_route_after_research_interrupt_approval_continues() -> None:
    state = graph_state(online_research_approved=True)
    assert route_after_research_interrupt(state) == NodeName.COLLECT_RESEARCH_EVIDENCE


def test_collect_research_evidence_node_appends_online_docs(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO)
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    def fake_load_settings():
        return settings

    class _Source:
        def __init__(self, title: str, location: str, summary: str) -> None:
            self.title = title
            self.location = location
            self.summary = summary
            self.excerpt = ""

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.collect_online_research_sources",
        lambda _request, _settings: [
            _Source(
                "LangGraph interrupts",
                "https://docs.langchain.com/oss/python/langgraph/interrupts",
                "Interrupts pause graph execution and resume with Command.",
            )
        ],
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.save_online_source_to_cache",
        lambda **_kwargs: False,
    )

    state = graph_state(
        online_research_approved=True,
        research_source_titles=["Local docs"],
        research_source_locations=["docs/ARCHITECTURE.md"],
        research_source_summaries=["Architecture and module map."],
    )

    result = collect_research_evidence_node(state)

    assert result["research_source_titles"] == ["Local docs", "LangGraph interrupts"]
    assert result["research_source_locations"] == [
        "docs/ARCHITECTURE.md",
        "https://docs.langchain.com/oss/python/langgraph/interrupts",
    ]
    assert result["research_online_sources_found"] == 1
    assert (
        "Research handoff summary: local_sources=1 online_sources=1 "
        "new_cache_notes=0 reused_cache_sources=1"
        in caplog.text
    )


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
        **_kwargs,
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
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

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
        **_kwargs,
    ):
        assert callable(progress_callback)
        progress_callback("Coding agent still running (about 1m 0s elapsed).")

        class Result:
            message = "done"
            returncode = 0
            success = True
            duration_seconds = 1.0
            changed_files_delta: tuple[str, ...] = ()
            command: list[str] = ["codex"]

            def summary(self) -> str:
                return "done"

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    state = run_coding_agent_node(
        graph_state(agent_instruction="Do the task"),
        execute_coding_agent_override=True,
        progress_callback=progress_messages.append,
    )

    assert progress_messages[0].startswith("Running coding agent (")
    assert "Coding agent still running (about 1m 0s elapsed)." in progress_messages
    assert state["coding_agent_result"] == "done"
    assert state["coding_agent_retry_count"] == 0


def test_request_plan_node_uses_generated_instruction_and_stores_stdout(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=False)
    captured: dict[str, object] = {}
    progress_messages: list[str] = []

    def fake_load_settings():
        return settings

    def fake_render_prompt(prompt_key: str, **replacements: str) -> str:
        assert prompt_key == "plan_request_instruction"
        assert replacements["formulated_task"] == "Build the plan"
        assert (
            replacements["correction_feedback"]
            == "\nPrevious plan was rejected. Correction needed:\nKeep it small."
        )
        return f"Plan for {replacements['formulated_task']}\n{replacements['correction_feedback']}"

    def fake_run_coding_agent(*, agent_instruction, project_root, settings, **_kwargs):
        captured["agent_instruction"] = agent_instruction
        captured["project_root"] = project_root
        captured["settings"] = settings

        class Result:
            stdout = "1. Do the thing\n2. Validate it"
            returncode = 0
            changed_files_delta: tuple[str, ...] = ()

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.render_prompt", fake_render_prompt)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    state = graph_state(
        formulated_task="Build the plan",
        plan_correction="Keep it small.",
    )

    result = request_plan_node(state, progress_callback=progress_messages.append)

    expected_instruction = (
        "Plan for Build the plan\n\nPrevious plan was rejected. Correction needed:\nKeep it small."
    )
    assert captured["agent_instruction"] == expected_instruction
    assert captured["project_root"] is not None
    assert captured["settings"].execute_coding_agent is False
    assert progress_messages == [
        "Requesting implementation plan from coding agent...",
        "Plan from coding agent:\n1. Do the thing\n2. Validate it",
    ]
    assert result["plan_text"] == "1. Do the thing\n2. Validate it"
    assert result["plan_approved"] is False
    assert result["plan_correction"] == ""


def test_graph_runs_to_disabled_coding_agent_result(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    def fake_load_settings():
        return settings

    class _SimpleResearchResult:
        is_complex = False
        sources_found = 0
        online_research_needed = False
        complexity_reason = "Simple task."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _SimpleResearchResult(),
    )
    app = build_graph(execute_coding_agent_override=False)

    result = app.invoke(graph_state())

    assert result["needs_approval"] is True
    assert (
        "AI risk review is off, so I need your approval before continuing."
        in result["approval_reason"]
    )
    assert result["approved"] is False
    assert result["agent_instruction"] == ""
    assert result["coding_agent_result"] == ""
    assert result["coding_agent_retry_count"] == 0


def test_already_approved_army_state_skips_approval_interrupt(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    class _SuccessResult:
        stdout = "1. Do the thing\n2. Validate it"
        stderr = ""
        returncode = 0
        success = True
        duration_seconds = 0.1
        changed_files_delta: tuple[str, ...] = ()
        command = ["codex"]
        message = "done"

        def summary(self) -> str:
            return "done"

    def fake_load_settings():
        return settings

    def fake_review_plan(*_args, **_kwargs):
        return PlanReviewDecision(approved=True, reason="Plan is fine.", correction="")

    class _SimpleResearchResult:
        is_complex = False
        sources_found = 0
        online_research_needed = False
        complexity_reason = "Simple task."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    def fail_if_approval_interrupt_called(*_args, **_kwargs):
        raise AssertionError(
            "approval interrupt should be skipped when the army has already approved"
        )

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _SimpleResearchResult(),
    )
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.review_plan", fake_review_plan)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", lambda **_kw: _SuccessResult()
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.approval_interrupt_node",
        fail_if_approval_interrupt_called,
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-already-approved-army"}}
    result = app.invoke(
        graph_state(
            request="Update README wording.",
            approved=True,
            approved_by="human-via-army",
            force_approval=False,
        ),
        config=thread_config,
    )

    assert result["approved"] is True
    assert result["approved_by"] == "human-via-army"
    assert result["coding_agent_result"] == "done"


def test_run_coding_agent_node_marks_restart_required_for_app_changes(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)

    def fake_load_settings():
        return settings

    class _ChangedResult:
        message = "done"
        returncode = 0
        duration_seconds = 0.5
        changed_files_delta = (
            "src/ai_tech_lead/coding_workflow_graph.py",
            "tests/test_coding_workflow_graph.py",
        )
        command = ["codex"]

        def summary(self) -> str:
            return self.message

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", lambda **_kw: _ChangedResult()
    )

    state = run_coding_agent_node(
        graph_state(agent_instruction="Do the task"),
        execute_coding_agent_override=True,
    )

    assert state["coding_agent_changed_files"] == _ChangedResult.changed_files_delta
    assert state["coding_agent_retry_count"] == 1  # no success attr → treated as failure


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
    assert "Coding agent success=" in caplog.text


def test_rejected_graph_resumes_without_coding_agent_result_index_error(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())

    def fake_load_settings():
        return settings

    class _SimpleResearchResult:
        is_complex = False
        sources_found = 0
        online_research_needed = False
        complexity_reason = "Simple task."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _SimpleResearchResult(),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-reject-path"}}

    app.invoke(graph_state(), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(Command(resume={"approved": False, "approved_by": ""}), config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approved"] is False
    assert final_state.values["coding_agent_result"] == ""


def test_rejected_research_interrupt_ends_graph(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    def fake_load_settings():
        return settings

    class _ResearchResult:
        is_complex = True
        sources_found = 0
        online_research_needed = True
        complexity_reason = "Needs sources."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    def fake_check_research_requirements(_request, _settings):
        return _ResearchResult()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        fake_check_research_requirements,
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("risk review should not run")
        ),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-research-reject-path"}}

    app.invoke(graph_state(), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.RESEARCH_INTERRUPT,)

    app.invoke(Command(resume={"approved": False}), config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["online_research_approved"] is False
    assert final_state.values["coding_agent_result"] == ""


def test_route_after_review_plan_approved() -> None:
    state = graph_state(plan_approved=True)
    assert route_after_review_plan(state) == NodeName.CREATE_AGENT_INSTRUCTION


def test_route_after_review_plan_rejected_first_time() -> None:
    state = graph_state(plan_approved=False, plan_rejection_count=1)
    assert route_after_review_plan(state) == NodeName.REQUEST_PLAN


def test_route_after_review_plan_rejected_twice_goes_to_human() -> None:
    state = graph_state(plan_approved=False, plan_rejection_count=2)
    assert route_after_review_plan(state) == NodeName.PLAN_INTERRUPT


def test_route_after_review_plan_reviewer_unavailable_goes_to_human() -> None:
    state = graph_state(plan_approved=False, plan_needs_human_review=True)
    assert route_after_review_plan(state) == NodeName.PLAN_INTERRUPT


def test_graph_has_no_clarification_gate_nodes() -> None:
    """Clarification check was removed; the compiled graph must not contain those nodes."""
    app = build_graph()
    node_names = set(app.get_graph().nodes.keys())
    assert "3_check_clarification" not in node_names
    assert "3b_clarification_interrupt" not in node_names


def test_review_risk_connects_directly_to_tech_lead_analyse() -> None:
    """After review_risk, the graph must route to tech_lead_analyse without a clarification gate."""
    app = build_graph()
    edges = [(e.source, e.target) for e in app.get_graph().edges]
    targets_from_review_risk = [t for s, t in edges if s == NodeName.REVIEW_RISK]
    assert NodeName.TECH_LEAD_ANALYSE in targets_from_review_risk
    assert "3_check_clarification" not in targets_from_review_risk
