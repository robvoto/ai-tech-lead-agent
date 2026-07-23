from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

from helpers import valid_settings_dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_workflow_graph import (
    NodeName,
    build_graph,
    build_initial_graph_state,
    collect_research_evidence_node,
    request_plan_node,
    resolve_context_node,
    route_after_resolve_context,
    understand_and_bound_request_node,
    route_after_approval,
    route_after_check_research,
    route_after_research_interrupt,
    route_after_review_plan,
    route_after_tech_lead_analyse,
    run_coding_agent_node,
)
from ai_tech_lead.plan_reviewer import PlanReviewDecision
from ai_tech_lead.risk_reviewer import RiskReviewDecision
from ai_tech_lead.tech_lead_analyst import TechLeadAnalysis


def graph_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "request": "Backlog item: JH-001\nTitle: Local placeholder",
        "brief": "",
        "force_approval": False,
        "research_evidence_required": False,
        "research_source_titles": [],
        "research_source_locations": [],
        "research_source_summaries": [],
        "online_research_approved": False,
        "orchestrator_input_required": False,
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
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


class _FakeAgentResult:
    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        message: str = "",
        returncode: int | None = 0,
        changed_files_delta: tuple[str, ...] = (),
        command: list[str] | None = None,
        duration_seconds: float = 0.1,
        success: bool | None = None,
        timed_out: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.message = message or stdout or stderr or ""
        self.returncode = returncode
        self.changed_files_delta = changed_files_delta
        self.command = command or []
        self.duration_seconds = duration_seconds
        self.timed_out = timed_out
        if success is not None:
            self.success = success

    def summary(self) -> str:
        return self.message or "no result"


def _research_result(
    *,
    is_complex: bool,
    sources_found: int = 0,
    online_research_needed: bool = False,
    complexity_reason: str = "Simple task.",
    titles: list[str] | None = None,
    locations: list[str] | None = None,
    summaries: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        is_complex=is_complex,
        sources_found=sources_found,
        online_research_needed=online_research_needed,
        complexity_reason=complexity_reason,
        usable_source_titles=titles or [],
        usable_source_locations=locations or [],
        usable_source_summaries=summaries or [],
    )


def test_graph_state_defaults_do_not_require_human_input() -> None:
    state = graph_state()

    assert state["orchestrator_input_required"] is False
    assert state["orchestrator_input_reason"] == ""
    assert state["orchestrator_input_question"] == ""
    assert state["task_feedback"] == []
    assert state["research_source_titles"] == []
    assert state["research_source_locations"] == []
    assert state["research_source_summaries"] == []
    assert state["coding_agent_retry_count"] == 0
    assert state["coding_agent_correction"] == ""


def test_graph_state_can_store_orchestrator_input_request_fields() -> None:
    state = graph_state(
        orchestrator_input_required=True,
        orchestrator_input_reason="Complex task needs external docs.",
        orchestrator_input_question="Fetch approved LangChain docs?",
    )

    assert state["orchestrator_input_required"] is True
    assert state["orchestrator_input_reason"] == "Complex task needs external docs."
    assert state["orchestrator_input_question"] == "Fetch approved LangChain docs?"


def test_build_initial_graph_state_is_canonical() -> None:
    state = build_initial_graph_state(
        "Verify workflow cleanup.",
        force_approval=True,
        approved=True,
        approved_by="subprocess",
        approval_reason="",
    )

    assert state["request"] == "Verify workflow cleanup."
    assert state["force_approval"] is True
    assert state["approved"] is True
    assert state["approved_by"] == "subprocess"
    assert state["plan_agent_stderr"] == ""
    assert state["coding_agent_retry_count"] == 0


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
    )
    assert route_after_check_research(state) == NodeName.REVIEW_RISK


def test_route_after_check_research_complex_with_insufficient_sources_gates() -> None:
    state = graph_state(
        research_evidence_required=True,
        online_research_approved=False,
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
            replacements["task_feedback"]
            == "\nTask feedback for this attempt:\n- Keep this docs-only."
        )
        assert (
            replacements["correction_feedback"]
            == "\nPrevious plan was rejected. Correction needed:\nKeep it small."
        )
        return (
            f"Plan for {replacements['formulated_task']}\n"
            f"{replacements['task_feedback']}\n"
            f"{replacements['correction_feedback']}"
        )

    def fake_run_coding_agent(*, agent_instruction, project_root, settings, **_kwargs):
        captured["agent_instruction"] = agent_instruction
        captured["project_root"] = project_root
        captured["settings"] = settings

        class Result:
            stdout = "1. Do the thing\n2. Validate it"
            stderr = ""
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
        task_feedback=["Keep this docs-only."],
        plan_correction="Keep it small.",
    )

    result = request_plan_node(state, progress_callback=progress_messages.append)

    expected_instruction = (
        "Plan for Build the plan\n"
        "\nTask feedback for this attempt:\n- Keep this docs-only.\n"
        "\nPrevious plan was rejected. Correction needed:\nKeep it small."
    )
    assert captured["agent_instruction"] == expected_instruction
    assert captured["project_root"] is not None
    assert captured["settings"].execute_coding_agent is False
    assert progress_messages == [
        "Requesting implementation plan from coding agent...",
        "Plan from coding agent:\n1. Do the thing\n2. Validate it",
    ]
    assert result["plan_text"] == "1. Do the thing\n2. Validate it"
    assert result["plan_agent_stderr"] == ""
    assert result["plan_approved"] is False
    assert result["plan_correction"] == ""


def test_request_plan_node_captures_stderr_when_stdout_empty(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=False)

    def fake_load_settings():
        return settings

    def fake_render_prompt(prompt_key: str, **replacements: str) -> str:
        return "some instruction"

    def fake_run_coding_agent(*, agent_instruction, project_root, settings, **_kwargs):
        class Result:
            stdout = ""
            stderr = "ERROR: You've hit your usage limit."
            returncode = 1
            changed_files_delta: tuple[str, ...] = ()

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.render_prompt", fake_render_prompt)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    result = request_plan_node(graph_state(), progress_callback=None)

    assert result["plan_text"] == ""
    assert result["plan_agent_stderr"] == "ERROR: You've hit your usage limit."


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


def test_already_approved_subprocess_state_skips_approval_interrupt(monkeypatch) -> None:
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
            "approval interrupt should be skipped when the subprocess caller has already approved"
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
    thread_config = {"configurable": {"thread_id": "test-already-approved-subprocess"}}
    result = app.invoke(
        graph_state(
            request="Update README wording.",
            approved=True,
            approved_by="human-via-subprocess",
            force_approval=False,
        ),
        config=thread_config,
    )

    assert result["approved"] is True
    assert result["approved_by"] == "human-via-subprocess"
    assert result["coding_agent_result"] == "done"


def test_run_coding_agent_node_infers_success_from_zero_returncode(monkeypatch) -> None:
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
    assert state["coding_agent_success"] is True
    assert state["coding_agent_retry_count"] == 0
    assert state["restart_required"] is True


def test_run_coding_agent_node_infers_failure_from_nonzero_returncode(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)

    def fake_load_settings():
        return settings

    class _FailedResult:
        message = "failed"
        returncode = 1
        duration_seconds = 0.5
        changed_files_delta: tuple[str, ...] = ()
        command = ["codex"]

        def summary(self) -> str:
            return self.message

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", lambda **_kw: _FailedResult()
    )

    state = run_coding_agent_node(
        graph_state(agent_instruction="Do the task"),
        execute_coding_agent_override=True,
    )

    assert state["coding_agent_success"] is False
    assert state["coding_agent_retry_count"] == 1


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


def test_workflow_scenario_approval_interrupt_resumes_to_success(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    plan_instructions: list[str] = []
    implementation_instructions: list[str] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _research_result(is_complex=False),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda _request: RiskReviewDecision(
            needs_approval=True,
            approval_reason="High-risk task needs explicit approval.",
            risk_level="HIGH",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.analyse_task",
        lambda **_kwargs: TechLeadAnalysis(
            task_statement="Update the runtime docs safely.",
            tech_direction="Keep the change scoped to documentation and tests.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **replacements: (
            f"PLAN::{replacements['formulated_task']}::"
            f"{replacements['task_feedback']}::{replacements['correction_feedback']}"
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_plan",
        lambda **_kwargs: PlanReviewDecision(
            approved=True,
            reason="Plan is bounded.",
            correction="",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_agent_instruction",
        lambda **kwargs: (
            f"IMPLEMENT::{kwargs['formulated_task']}::{kwargs['brief']}::"
            f"{' | '.join(kwargs['task_feedback'])}::{kwargs['agent_correction'] or ''}"
        ),
    )

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            plan_instructions.append(agent_instruction)
            return _FakeAgentResult(
                stdout="1. Inspect docs\n2. Update wording\nDone when: docs reflect runtime.",
                returncode=0,
            )
        implementation_instructions.append(agent_instruction)
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("docs/RUNTIME_RUNBOOK.md",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent",
        fake_run_coding_agent,
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-approval-success"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(
        Command(resume={"approved": True, "approved_by": "telegram-operator"}),
        config=thread_config,
    )

    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["approved"] is True
    assert final_state.values["approved_by"] == "telegram-operator"
    assert final_state.values["plan_text"].startswith("1. Inspect docs")
    assert final_state.values["coding_agent_success"] is True
    assert plan_instructions == ["PLAN::Update the runtime docs safely.::::"]
    assert implementation_instructions == [
        (
            "IMPLEMENT::Update the runtime docs safely.::"
            "Keep the change scoped to documentation and tests.::::"
        )
    ]


def test_workflow_scenario_research_interrupt_resumes_to_success(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    research_evidence_seen: list[list[str]] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _research_result(
            is_complex=True,
            sources_found=1,
            online_research_needed=True,
            complexity_reason="LangGraph interrupt semantics should be checked.",
            titles=["Local workflow notes"],
            locations=["docs/GRAPH_WORKFLOW.md"],
            summaries=["Current local workflow and interrupt notes."],
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.collect_online_research_sources",
        lambda _request, _settings: [
            SimpleNamespace(
                title="LangGraph interrupts",
                location="https://docs.langchain.com/oss/python/langgraph/interrupts",
                summary="Interrupts resume with Command objects.",
                excerpt="",
            )
        ],
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.save_online_source_to_cache",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda _request: RiskReviewDecision(
            needs_approval=False,
            approval_reason="Bounded research-backed task.",
            risk_level="LOW",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.analyse_task",
        lambda **_kwargs: TechLeadAnalysis(
            task_statement="Verify the interrupt workflow docs.",
            tech_direction="Use local notes plus the approved LangGraph interrupts doc.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **replacements: f"PLAN::{replacements['formulated_task']}",
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_plan",
        lambda **_kwargs: PlanReviewDecision(True, "Plan is bounded.", ""),
    )

    def fake_build_agent_instruction(**kwargs):
        research_evidence_seen.append(list(kwargs["research_evidence"]))
        return "IMPLEMENT::research"

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_agent_instruction",
        fake_build_agent_instruction,
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent",
        lambda **kwargs: (
            _FakeAgentResult(stdout="1. Review docs\nDone when: workflow is documented.")
            if kwargs["agent_instruction"].startswith("PLAN::")
            else _FakeAgentResult(
                message="implemented",
                returncode=0,
                changed_files_delta=("docs/GRAPH_WORKFLOW.md",),
                command=["codex"],
            )
        ),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-research-success"}}

    app.invoke(graph_state(request="Verify interrupt workflow docs."), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.RESEARCH_INTERRUPT,)

    app.invoke(Command(resume={"approved": True}), config=thread_config)

    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["online_research_approved"] is True
    assert final_state.values["research_source_titles"] == [
        "Local workflow notes",
        "LangGraph interrupts",
    ]
    assert research_evidence_seen == [
        [
            (
                "Local workflow notes | docs/GRAPH_WORKFLOW.md | "
                "Current local workflow and interrupt notes."
            ),
            (
                "LangGraph interrupts | "
                "https://docs.langchain.com/oss/python/langgraph/interrupts | "
                "Interrupts resume with Command objects."
            ),
        ]
    ]
    assert final_state.values["coding_agent_success"] is True


def test_workflow_scenario_plan_guidance_resume_feeds_next_plan_attempt(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    plan_instructions: list[str] = []
    review_decisions = iter(
        [
            PlanReviewDecision(False, "Too broad.", "Keep it to one file."),
            PlanReviewDecision(False, "Still too broad.", "Limit it to docs or tests."),
            PlanReviewDecision(True, "Now bounded.", ""),
        ]
    )

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _research_result(is_complex=False),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda _request: RiskReviewDecision(False, "Safe local docs/test work.", "LOW"),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.analyse_task",
        lambda **_kwargs: TechLeadAnalysis(
            task_statement="Tighten the workflow docs.",
            tech_direction="Keep the task bounded to one docs or tests slice.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **replacements: (
            f"PLAN::{replacements['formulated_task']}::"
            f"{replacements['task_feedback']}::{replacements['correction_feedback']}"
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_plan",
        lambda **_kwargs: next(review_decisions),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_agent_instruction",
        lambda **kwargs: (
            f"IMPLEMENT::{kwargs['formulated_task']}::"
            f"{' | '.join(kwargs['task_feedback'])}"
        ),
    )

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            plan_instructions.append(agent_instruction)
            attempt = len(plan_instructions)
            return _FakeAgentResult(
                stdout=f"{attempt}. Draft plan attempt {attempt}\nDone when: bounded.",
                returncode=0,
            )
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("docs/GRAPH_WORKFLOW.md",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent",
        fake_run_coding_agent,
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-plan-guidance-success"}}

    app.invoke(graph_state(request="Tighten the workflow docs."), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.PLAN_INTERRUPT,)
    assert state_snapshot.values["plan_rejection_count"] == 2

    app.invoke(
        Command(resume={"text": "Keep scope to docs/tests only."}),
        config=thread_config,
    )

    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["task_feedback"] == ["Keep scope to docs/tests only."]
    assert final_state.values["coding_agent_success"] is True
    assert len(plan_instructions) == 3
    assert "Keep it to one file." in plan_instructions[1]
    assert "Keep scope to docs/tests only." in plan_instructions[2]


def test_workflow_scenario_failure_guidance_resume_retries_to_success(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    implementation_instructions: list[str] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings: _research_result(is_complex=False),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda _request: RiskReviewDecision(False, "Safe local tests-only work.", "LOW"),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.analyse_task",
        lambda **_kwargs: TechLeadAnalysis(
            task_statement="Improve workflow tests.",
            tech_direction="Keep the change to workflow tests only.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **replacements: f"PLAN::{replacements['formulated_task']}",
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_plan",
        lambda **_kwargs: PlanReviewDecision(True, "Plan is bounded.", ""),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_agent_instruction",
        lambda **kwargs: (
            f"IMPLEMENT::{kwargs['formulated_task']}::{' | '.join(kwargs['task_feedback'])}::"
            f"{kwargs['agent_correction'] or ''}"
        ),
    )

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            return _FakeAgentResult(stdout="1. Update tests\nDone when: retries are covered.")

        implementation_instructions.append(agent_instruction)
        attempt = len(implementation_instructions)
        if attempt == 1:
            return _FakeAgentResult(message="lint failed", returncode=1, command=["codex"])
        if attempt == 2:
            return _FakeAgentResult(message="tests failed", returncode=1, command=["codex"])
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("tests/test_coding_workflow_graph.py",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent",
        fake_run_coding_agent,
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-failure-guidance-success"}}

    app.invoke(graph_state(request="Improve workflow tests."), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.FAILURE_INTERRUPT,)
    assert state_snapshot.values["coding_agent_retry_count"] == 2

    app.invoke(
        Command(resume={"text": "Narrow the fix to tests first."}),
        config=thread_config,
    )

    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["task_feedback"] == ["Narrow the fix to tests first."]
    assert final_state.values["coding_agent_success"] is True
    assert len(implementation_instructions) == 3
    assert "lint failed" in implementation_instructions[1]
    assert "Narrow the fix to tests first." in implementation_instructions[2]


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


def test_request_context_is_resolved_before_research(monkeypatch) -> None:
    state = graph_state(
        request="Code AF-052 for Agent Factory",
        supplied_context={
            "project_reference": {"project_key": "agent-factory"},
            "backlog_reference": {
                "item_id": "AF-052",
                "title": "Implement validation",
                "body": "Acceptance Criteria: bounded validation.",
            },
        },
    )

    understood = understand_and_bound_request_node(state)
    state.update(understood)
    resolved = resolve_context_node(state)
    state.update(resolved)

    assert route_after_resolve_context(state) == NodeName.CHECK_RESEARCH
    assert state["unresolved_references"] == []
    assert "bounded validation" in state["bounded_request"]


def test_unresolved_reference_routes_to_clarification_before_research() -> None:
    state = graph_state(
        request="Code AF-052 for Agent Factory",
        supplied_context={},
    )

    state.update(understand_and_bound_request_node(state))
    state.update(resolve_context_node(state))

    assert route_after_resolve_context(state) == NodeName.END_NODE
    assert state["orchestrator_input_required"] is True
    assert state["orchestrator_input_question"] == (
        "What does AF-052 refer to, and where should I retrieve it from?"
    )
