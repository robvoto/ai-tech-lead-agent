from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import valid_settings_dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_workflow_graph import (
    CONTEXT_CLARIFICATION_MAX_RETRIES,
    NodeName,
    build_graph,
    build_initial_graph_state,
    check_code_look_need_node,
    check_research_node,
    codex_reads_code_node,
    collect_research_evidence_node,
    create_agent_instruction_node,
    discover_research_source_node,
    end_node,
    project_scope_decision_node,
    read_and_classify_request_node,
    request_plan_node,
    resolve_context_node,
    route_after_approval,
    route_after_check_code_look_need,
    route_after_check_research,
    route_after_read_and_classify_request,
    route_after_research_interrupt,
    route_after_resolve_context,
    route_after_review_plan,
    route_after_review_risk,
    route_after_tech_lead_analyse,
    route_after_verify_completion,
    run_coding_agent_node,
)
from ai_tech_lead.completion_verifier import CompletionVerificationDecision
from ai_tech_lead.plan_reviewer import PlanReviewDecision
from ai_tech_lead.risk_reviewer import RiskReviewDecision
from ai_tech_lead.target_project_context import BacklogItemContext, TargetProjectContext
from ai_tech_lead.tech_lead_analyst import TechLeadAnalysis


def _mock_verify_completion_complete(monkeypatch) -> None:
    """Skip the real AI Tech Lead completion check in tests that only exercise routing."""

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.verify_completion",
        lambda **_kwargs: CompletionVerificationDecision(
            status="complete", reason="Verified for test purposes.", correction=""
        ),
    )


def graph_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "request": "Backlog item: JH-001\nTitle: Local placeholder",
        "target_project_context": None,
        "brief": "",
        "force_approval": False,
        "research_evidence_required": False,
        "research_gap_question": "",
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
        "approval_action": "",
        "approval_revision_count": 0,
        "approval_last_question": "",
        "approval_last_answer": "",
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
    has_gap: bool,
    gap_question: str = "",
    sources_found: int = 0,
    online_research_needed: bool = False,
    gap_reason: str = "No external knowledge gap identified.",
    titles: list[str] | None = None,
    locations: list[str] | None = None,
    summaries: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        has_gap=has_gap,
        gap_question=gap_question,
        sources_found=sources_found,
        online_research_needed=online_research_needed,
        gap_reason=gap_reason,
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
    assert route_after_review_risk(graph_state(needs_approval=True)) == "approval required"
    assert (
        route_after_review_risk(graph_state(needs_approval=True, approved=True))
        == "already approved"
    )
    assert route_after_review_risk(graph_state(needs_approval=False)) == "low risk"
    assert (
        route_after_approval(graph_state(approved=True, approval_action="approve"))
        == "approved"
    )
    assert (
        route_after_approval(graph_state(approved=False, approval_action="cancel"))
        == "cancelled"
    )


def test_route_after_tech_lead_analyse_checks_research_once() -> None:
    """First pass (research not yet checked) routes to CHECK_RESEARCH."""

    state = graph_state(research_checked=False)
    assert route_after_tech_lead_analyse(state) == "check research"


def test_route_after_tech_lead_analyse_skips_research_second_time() -> None:
    """Once CHECK_RESEARCH has run (or on an approval revision), go straight to risk review."""

    state = graph_state(research_checked=True)
    assert route_after_tech_lead_analyse(state) == "analysis complete"


def test_route_after_check_research_simple_task_skips_gate() -> None:
    state = graph_state(research_evidence_required=False, online_research_approved=False)
    assert route_after_check_research(state) == "no research needed"


def test_route_after_check_research_complex_with_sufficient_sources_skips_gate() -> None:
    state = graph_state(
        research_evidence_required=True,
        online_research_approved=True,
    )
    assert route_after_check_research(state) == "no research needed"


def test_route_after_check_research_complex_with_insufficient_sources_gates() -> None:
    state = graph_state(
        research_evidence_required=True,
        online_research_approved=False,
    )
    assert route_after_check_research(state) == "research needed"


def test_route_after_research_interrupt_rejection_ends_workflow() -> None:
    state = graph_state(online_research_approved=False)
    assert route_after_research_interrupt(state) == "declined"


def test_route_after_research_interrupt_approval_continues() -> None:
    state = graph_state(online_research_approved=True)
    assert route_after_research_interrupt(state) == "approved"


def test_check_research_node_passes_resolved_project_root_as_code_context_root(
    monkeypatch,
) -> None:
    """The code-context scan must target the resolved task project, not this repo."""

    settings = parse_settings(valid_settings_dict())
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)

    seen_kwargs: dict = {}

    def fake_check_research_requirements(request, _settings, **kwargs):
        seen_kwargs.update(kwargs)
        return _research_result(has_gap=False)

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        fake_check_research_requirements,
    )

    state = graph_state(resolved_project_root="/repos/some-other-project")
    check_research_node(state)

    assert seen_kwargs["code_context_root"] == "/repos/some-other-project"


def test_check_research_node_code_context_root_none_when_unresolved(monkeypatch) -> None:
    settings = parse_settings(valid_settings_dict())
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)

    seen_kwargs: dict = {}

    def fake_check_research_requirements(request, _settings, **kwargs):
        seen_kwargs.update(kwargs)
        return _research_result(has_gap=False)

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        fake_check_research_requirements,
    )

    state = graph_state()
    check_research_node(state)

    assert seen_kwargs["code_context_root"] is None


def test_discover_research_source_node_names_candidate_in_question(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), research_discovery_enabled=True)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.discover_official_source",
        lambda _gap_question, _settings, **_kwargs: SimpleNamespace(
            url="https://core.telegram.org/bots/api",
            title="Telegram Bot API",
        ),
    )

    state = graph_state(
        research_gap_question="What are Telegram Bot API's official retry rules?",
        orchestrator_input_question="Fetch from the approved online source registry?",
    )

    result = discover_research_source_node(state)

    assert result["discovered_source_url"] == "https://core.telegram.org/bots/api"
    assert result["discovered_source_title"] == "Telegram Bot API"
    assert "https://core.telegram.org/bots/api" in result["orchestrator_input_question"]
    assert "Telegram Bot API" in result["orchestrator_input_question"]


def test_discover_research_source_node_falls_back_when_no_candidate(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), research_discovery_enabled=False)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.discover_official_source",
        lambda _gap_question, _settings, **_kwargs: None,
    )

    original_question = (
        "Fetch from the approved online source registry "
        "(bounded to: docs.langchain.com)?"
    )
    state = graph_state(
        research_gap_question="What are Telegram Bot API's official retry rules?",
        orchestrator_input_question=original_question,
    )

    result = discover_research_source_node(state)

    assert result["research_policy_profiles"] == ["telegram"]
    assert "core.telegram.org" in result["research_trusted_domains"]
    assert result["research_seed_urls"][0] == "https://core.telegram.org/bots/api"
    assert state["orchestrator_input_question"] == original_question


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
        lambda _request, _settings, **_kwargs: [
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


def test_collect_research_evidence_node_passes_discovered_url_as_extra(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.save_online_source_to_cache",
        lambda **_kwargs: False,
    )

    seen_kwargs: dict = {}

    def fake_collect_online_research_sources(request, _settings, **kwargs):
        seen_kwargs["request"] = request
        seen_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.collect_online_research_sources",
        fake_collect_online_research_sources,
    )

    state = graph_state(
        online_research_approved=True,
        discovered_source_url="https://core.telegram.org/bots/api",
    )

    collect_research_evidence_node(state)

    assert seen_kwargs["extra_urls"] == ["https://core.telegram.org/bots/api"]


def test_collect_research_evidence_node_no_extra_urls_when_nothing_discovered(
    monkeypatch,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.save_online_source_to_cache",
        lambda **_kwargs: False,
    )

    seen_kwargs: dict = {}

    def fake_collect_online_research_sources(request, _settings, **kwargs):
        seen_kwargs["request"] = request
        seen_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.collect_online_research_sources",
        fake_collect_online_research_sources,
    )

    state = graph_state(online_research_approved=True)

    collect_research_evidence_node(state)

    assert seen_kwargs["extra_urls"] is None


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
        has_gap = False
        gap_question = ""
        sources_found = 0
        online_research_needed = False
        gap_reason = "No external knowledge gap identified."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings, **_kwargs: _SimpleResearchResult(),
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
        has_gap = False
        gap_question = ""
        sources_found = 0
        online_research_needed = False
        gap_reason = "No external knowledge gap identified."
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
        lambda _request, _settings, **_kwargs: _SimpleResearchResult(),
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
        has_gap = False
        gap_question = ""
        sources_found = 0
        online_research_needed = False
        gap_reason = "No external knowledge gap identified."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings, **_kwargs: _SimpleResearchResult(),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-reject-path"}}

    app.invoke(graph_state(), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(Command(resume={"action": "cancel"}), config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approved"] is False
    assert final_state.values["approval_action"] == "cancel"
    assert final_state.values["coding_agent_result"] == ""


def test_rejected_research_interrupt_ends_graph(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    def fake_load_settings():
        return settings

    class _ResearchResult:
        has_gap = True
        gap_question = "What are the official retry semantics for this integration?"
        sources_found = 0
        online_research_needed = True
        gap_reason = "Needs sources."
        usable_source_titles: list[str] = []
        usable_source_locations: list[str] = []
        usable_source_summaries: list[str] = []

    def fake_check_research_requirements(_request, _settings, **_kwargs):
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


def test_context_clarification_interrupt_resumes_and_continues(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
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
            task_statement="Code AF-052 for Agent Factory.",
            tech_direction="Keep the change scoped to the agent-factory repo.",
        ),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-context-clarification"}}

    app.invoke(
        graph_state(request="Code AF-052 for Agent Factory"),
        config=thread_config,
    )
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.CONTEXT_CLARIFICATION_INTERRUPT,)
    pending_interrupt = state_snapshot.tasks[0].interrupts[0].value
    assert pending_interrupt["kind"] == "context_clarification"
    assert "AF-052" in pending_interrupt["question"]

    app.invoke(
        Command(resume="AF-052 is the agent-factory repo, already registered."),
        config=thread_config,
    )

    final_state = app.get_state(thread_config)
    assert final_state.next == (NodeName.APPROVAL_INTERRUPT,)
    assert final_state.values["unresolved_references"] == []
    assert final_state.values["orchestrator_input_required"] is False
    assert final_state.values["context_clarification_retry_count"] == 1
    assert "Clarification for AF-052" in final_state.values["bounded_request"]


def test_context_clarification_fails_clearly_after_retry_limit() -> None:
    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "test-context-clarification-exhausted"}}

    app.invoke(
        graph_state(request="Code AF-052 for Agent Factory"),
        config=thread_config,
    )
    state_snapshot = app.get_state(thread_config)
    assert state_snapshot.next == (NodeName.CONTEXT_CLARIFICATION_INTERRUPT,)

    # An empty answer leaves the reference unresolved (resolve_request_context only
    # resolves it given non-empty clarification text), so the one allowed round is used
    # up without success and the workflow must fail clearly instead of asking again.
    app.invoke(Command(resume={"text": ""}), config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["context_clarification_exhausted"] is True
    assert final_state.values["unresolved_references"] == ["AF-052"]


def test_route_after_review_plan_approved() -> None:
    state = graph_state(plan_approved=True)
    assert route_after_review_plan(state) == "plan approved"


def test_route_after_review_plan_rejected_first_time() -> None:
    state = graph_state(plan_approved=False, plan_rejection_count=1)
    assert route_after_review_plan(state) == "revise plan"


def test_route_after_review_plan_rejected_twice_goes_to_human() -> None:
    state = graph_state(plan_approved=False, plan_rejection_count=2)
    assert route_after_review_plan(state) == "human review needed"


def test_route_after_review_plan_reviewer_unavailable_goes_to_human() -> None:
    state = graph_state(plan_approved=False, plan_needs_human_review=True)
    assert route_after_review_plan(state) == "human review needed"


def test_route_after_verify_completion_complete_goes_to_end() -> None:
    state = graph_state(verification_status="complete")
    assert route_after_verify_completion(state) == "verification finished"


def test_route_after_verify_completion_failed_goes_to_end() -> None:
    state = graph_state(verification_status="failed")
    assert route_after_verify_completion(state) == "verification finished"


def test_route_after_verify_completion_correction_required_loops_back() -> None:
    state = graph_state(verification_status="correction_required")
    assert route_after_verify_completion(state) == "correction needed"


def test_route_after_verify_completion_human_required_goes_to_interrupt() -> None:
    state = graph_state(verification_status="human_verification_required")
    assert route_after_verify_completion(state) == "human verification needed"


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
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
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

    _mock_verify_completion_complete(monkeypatch)

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-approval-success"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(
        Command(resume={"action": "approve", "approved_by": "telegram-operator"}),
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


def _approval_loop_settings():
    return replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )


def _patch_common_approval_loop_mocks(monkeypatch, settings, *, analyse_task_fn=None):
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
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
        analyse_task_fn
        or (
            lambda **_kwargs: TechLeadAnalysis(
                task_statement="Update the runtime docs safely.",
                tech_direction="Keep the change scoped to documentation and tests.",
            )
        ),
    )


def test_workflow_scenario_request_changes_loops_back_with_feedback(monkeypatch) -> None:
    settings = _approval_loop_settings()
    seen_task_feedback: list[list[str]] = []

    def fake_analyse_task(**kwargs):
        feedback = list(kwargs["task_feedback"])
        seen_task_feedback.append(feedback)
        if feedback:
            return TechLeadAnalysis(
                task_statement="Update the runtime docs, docs-only as requested.",
                tech_direction="Keep the change scoped to documentation only.",
            )
        return TechLeadAnalysis(
            task_statement="Update the runtime docs safely.",
            tech_direction="Keep the change scoped to documentation and tests.",
        )

    _patch_common_approval_loop_mocks(monkeypatch, settings, analyse_task_fn=fake_analyse_task)

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-request-changes"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    state_snapshot = app.get_state(thread_config)
    assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(
        Command(
            resume={"action": "request_changes", "feedback": "Keep this to docs only."}
        ),
        config=thread_config,
    )

    state_snapshot = app.get_state(thread_config)

    # Still paused at the approval interrupt with the regenerated proposal.
    assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)
    assert state_snapshot.values["task_feedback"] == ["Keep this to docs only."]
    assert state_snapshot.values["approval_revision_count"] == 1
    assert state_snapshot.values["approved"] is False
    assert state_snapshot.values["formulated_task"] == (
        "Update the runtime docs, docs-only as requested."
    )
    # Tech Lead Analysis now runs twice on the first pass (once to check whether
    # research is needed, once more after that check completes) before the
    # human ever sees the approval interrupt, then once more on the revision.
    assert seen_task_feedback == [[], [], ["Keep this to docs only."]]


def test_workflow_scenario_ask_question_preserves_state_and_reasks(monkeypatch) -> None:
    settings = _approval_loop_settings()
    _patch_common_approval_loop_mocks(monkeypatch, settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.collect_code_context",
        lambda *_args, **_kwargs: [],
    )
    seen_questions: list[str] = []

    def fake_answer_operator_question(*, question, **_kwargs):
        seen_questions.append(question)
        return "Only docs/RUNTIME_RUNBOOK.md will change."

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.answer_operator_question",
        fake_answer_operator_question,
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-ask-question"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    before_snapshot = app.get_state(thread_config)
    assert before_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(
        Command(
            resume={"action": "ask_question", "question": "Which files will this touch?"}
        ),
        config=thread_config,
    )

    after_snapshot = app.get_state(thread_config)

    assert after_snapshot.next == (NodeName.APPROVAL_INTERRUPT,)
    assert seen_questions == ["Which files will this touch?"]
    # Task/project state must be untouched by asking a question.
    assert after_snapshot.values["formulated_task"] == before_snapshot.values["formulated_task"]
    assert after_snapshot.values["brief"] == before_snapshot.values["brief"]
    assert after_snapshot.values["approval_reason"] == before_snapshot.values["approval_reason"]
    assert after_snapshot.values["task_feedback"] == before_snapshot.values["task_feedback"]
    assert after_snapshot.values["approval_last_question"] == "Which files will this touch?"
    assert after_snapshot.values["approval_last_answer"] == (
        "Only docs/RUNTIME_RUNBOOK.md will change."
    )
    # The re-shown interrupt displays the same four choices, with the answer visible.
    interrupt_value = after_snapshot.tasks[0].interrupts[0].value
    assert interrupt_value["last_question"] == "Which files will this touch?"
    assert interrupt_value["last_answer"] == "Only docs/RUNTIME_RUNBOOK.md will change."


def test_workflow_scenario_cancel_ends_workflow(monkeypatch) -> None:
    settings = _approval_loop_settings()
    _patch_common_approval_loop_mocks(monkeypatch, settings)

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-cancel"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    assert app.get_state(thread_config).next == (NodeName.APPROVAL_INTERRUPT,)

    app.invoke(Command(resume={"action": "cancel"}), config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approved"] is False
    assert final_state.values["approval_action"] == "cancel"
    assert final_state.values["coding_agent_result"] == ""


def test_workflow_scenario_unrecognized_resume_fails_closed_to_cancel(monkeypatch) -> None:
    settings = _approval_loop_settings()
    _patch_common_approval_loop_mocks(monkeypatch, settings)

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-malformed-resume"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    assert app.get_state(thread_config).next == (NodeName.APPROVAL_INTERRUPT,)

    # Old-contract / malformed resume values must never be treated as approval.
    app.invoke(Command(resume={"approved": True}), config=thread_config)

    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approved"] is False
    assert final_state.values["approval_action"] == "cancel"


def test_workflow_scenario_revision_cap_stops_safely(monkeypatch) -> None:
    settings = _approval_loop_settings()
    _patch_common_approval_loop_mocks(monkeypatch, settings)

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-revision-cap"}}

    app.invoke(graph_state(request="Update the runtime docs."), config=thread_config)
    assert app.get_state(thread_config).next == (NodeName.APPROVAL_INTERRUPT,)

    for cycle in range(1, 5):
        app.invoke(
            Command(resume={"action": "request_changes", "feedback": f"Round {cycle}."}),
            config=thread_config,
        )
        state_snapshot = app.get_state(thread_config)
        assert state_snapshot.next == (NodeName.APPROVAL_INTERRUPT,), f"cycle {cycle}"
        assert state_snapshot.values["approval_revision_count"] == cycle

    # 5th request-changes hits the cap and stops instead of looping again.
    app.invoke(
        Command(resume={"action": "request_changes", "feedback": "Round 5."}),
        config=thread_config,
    )
    final_state = app.get_state(thread_config)
    assert final_state.next == ()
    assert final_state.values["approval_revision_count"] == 5
    assert final_state.values["approved"] is False


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
        lambda _request, _settings, **_kwargs: _research_result(
            has_gap=True,
            gap_question="What are LangGraph's official interrupt semantics?",
            sources_found=1,
            online_research_needed=True,
            gap_reason="LangGraph interrupt semantics should be checked.",
            titles=["Local workflow notes"],
            locations=["docs/GRAPH_WORKFLOW.md"],
            summaries=["Current local workflow and interrupt notes."],
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.collect_online_research_sources",
        lambda _request, _settings, **_kwargs: [
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
    _mock_verify_completion_complete(monkeypatch)
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
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
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
    _mock_verify_completion_complete(monkeypatch)
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
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
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
    _mock_verify_completion_complete(monkeypatch)
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


def _patch_common_success_path_mocks(monkeypatch, settings) -> None:
    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda _request: RiskReviewDecision(False, "Safe local tests-only work.", "LOW"),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.analyse_task",
        lambda **_kwargs: TechLeadAnalysis(
            task_statement="Add a logout button.",
            tech_direction="Small UI change only.",
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
        lambda **kwargs: f"IMPLEMENT::{kwargs['agent_correction'] or 'first attempt'}",
    )


def test_workflow_scenario_completion_verification_correction_then_success(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    _patch_common_success_path_mocks(monkeypatch, settings)

    implementation_instructions: list[str] = []

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            return _FakeAgentResult(stdout="1. Add button\nDone when: logout works.")
        implementation_instructions.append(agent_instruction)
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("src/app/settings.py",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    verification_calls: list[str] = []

    def fake_verify_completion(**kwargs):
        verification_calls.append(kwargs.get("prior_correction", ""))
        if len(verification_calls) == 1:
            return CompletionVerificationDecision(
                status="correction_required",
                reason="Logout button does not sign the user out.",
                correction="Wire the click handler to the sign-out endpoint.",
            )
        return CompletionVerificationDecision(
            status="complete", reason="Sign-out now works.", correction=""
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.verify_completion", fake_verify_completion
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-verification-correction-success"}}

    app.invoke(graph_state(request="Add a logout button."), config=thread_config)
    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["verification_status"] == "complete"
    assert final_state.values["verification_attempt_count"] == 1
    assert len(implementation_instructions) == 2
    assert "Wire the click handler to the sign-out endpoint." in implementation_instructions[1]


def test_workflow_scenario_completion_verification_correction_limit_reached_ends_failed(
    monkeypatch,
) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    _patch_common_success_path_mocks(monkeypatch, settings)

    implementation_instructions: list[str] = []

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            return _FakeAgentResult(stdout="1. Add button\nDone when: logout works.")
        implementation_instructions.append(agent_instruction)
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("src/app/settings.py",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    def fake_verify_completion(**_kwargs):
        return CompletionVerificationDecision(
            status="correction_required",
            reason="Logout button still does not sign the user out.",
            correction="Wire the click handler to the sign-out endpoint.",
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.verify_completion", fake_verify_completion
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-verification-correction-limit"}}

    app.invoke(graph_state(request="Add a logout button."), config=thread_config)
    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["verification_status"] == "failed"
    assert final_state.values["verification_attempt_count"] == 1
    assert len(implementation_instructions) == 2


def test_workflow_scenario_completion_verification_human_required_confirms_complete(
    monkeypatch,
) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    _patch_common_success_path_mocks(monkeypatch, settings)

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            return _FakeAgentResult(stdout="1. Add button\nDone when: logout works.")
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("src/app/settings.py",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.verify_completion",
        lambda **_kwargs: CompletionVerificationDecision(
            status="human_verification_required",
            reason="Requires a visual check of the button placement.",
            correction="",
        ),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-verification-human-confirm"}}

    app.invoke(graph_state(request="Add a logout button."), config=thread_config)
    state_snapshot = app.get_state(thread_config)

    assert state_snapshot.next == (NodeName.COMPLETION_VERIFICATION_INTERRUPT,)

    app.invoke(Command(resume={"decision": "confirm_complete"}), config=thread_config)
    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["verification_status"] == "complete"


def test_workflow_scenario_completion_verification_human_required_rejects(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    _patch_common_success_path_mocks(monkeypatch, settings)

    def fake_run_coding_agent(*, agent_instruction, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            return _FakeAgentResult(stdout="1. Add button\nDone when: logout works.")
        return _FakeAgentResult(
            message="implemented",
            returncode=0,
            changed_files_delta=("src/app/settings.py",),
            command=["codex"],
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.verify_completion",
        lambda **_kwargs: CompletionVerificationDecision(
            status="human_verification_required",
            reason="Requires a visual check of the button placement.",
            correction="",
        ),
    )

    app = build_graph(checkpointer_storage=MemorySaver(), execute_coding_agent_override=False)
    thread_config = {"configurable": {"thread_id": "workflow-verification-human-reject"}}

    app.invoke(graph_state(request="Add a logout button."), config=thread_config)

    app.invoke(
        Command(resume={"decision": "reject", "text": "Button is misaligned."}),
        config=thread_config,
    )
    final_state = app.get_state(thread_config)

    assert final_state.next == ()
    assert final_state.values["verification_status"] == "failed"
    assert final_state.values["verification_reason"] == "Button is misaligned."


def test_graph_has_no_clarification_gate_nodes() -> None:
    """Clarification check was removed; the compiled graph must not contain those nodes."""
    app = build_graph()
    node_names = set(app.get_graph().nodes.keys())
    assert "3_check_clarification" not in node_names
    assert "3b_clarification_interrupt" not in node_names


def test_tech_lead_analyse_runs_before_check_research() -> None:
    """Analysis runs first so the research gap is judged against the formulated task."""
    app = build_graph()
    edges = [(e.source, e.target) for e in app.get_graph().edges]
    targets_from_resolve_context = [t for s, t in edges if s == NodeName.RESOLVE_CONTEXT]
    assert NodeName.CHECK_CODE_LOOK_NEED in targets_from_resolve_context
    assert NodeName.TECH_LEAD_ANALYSE not in targets_from_resolve_context
    assert NodeName.CHECK_RESEARCH not in targets_from_resolve_context

    targets_from_tech_lead_analyse = [t for s, t in edges if s == NodeName.TECH_LEAD_ANALYSE]
    assert NodeName.CHECK_RESEARCH in targets_from_tech_lead_analyse
    assert NodeName.REVIEW_RISK in targets_from_tech_lead_analyse


def test_check_code_look_need_routes_to_codex_or_straight_to_analyse() -> None:
    """Confirms the code-look step sits between context resolution and analysis."""
    app = build_graph()
    edges = [(e.source, e.target) for e in app.get_graph().edges]
    targets_from_check = [t for s, t in edges if s == NodeName.CHECK_CODE_LOOK_NEED]
    assert NodeName.CODEX_READS_CODE in targets_from_check
    assert NodeName.TECH_LEAD_ANALYSE in targets_from_check

    targets_from_codex_read = [t for s, t in edges if s == NodeName.CODEX_READS_CODE]
    assert targets_from_codex_read == [NodeName.TECH_LEAD_ANALYSE]


def test_route_after_check_code_look_need() -> None:
    assert route_after_check_code_look_need(graph_state(code_look_needed=True)) == "needed"
    assert route_after_check_code_look_need(graph_state(code_look_needed=False)) == "not needed"


def test_review_risk_connects_to_approval_routing_not_tech_lead_analyse() -> None:
    """Risk review now runs after analysis and routes to approval/plan, not back to analysis."""
    app = build_graph()
    edges = [(e.source, e.target) for e in app.get_graph().edges]
    targets_from_review_risk = [t for s, t in edges if s == NodeName.REVIEW_RISK]
    assert NodeName.TECH_LEAD_ANALYSE not in targets_from_review_risk
    assert NodeName.APPROVAL_INTERRUPT in targets_from_review_risk
    assert NodeName.REQUEST_PLAN in targets_from_review_risk


def test_read_and_classify_request_rejects_empty_request() -> None:
    with pytest.raises(ValueError):
        read_and_classify_request_node(graph_state(request="   "))


def test_read_and_classify_request_defaults_relevant_when_ai_disabled(monkeypatch) -> None:
    """Relevance check fails open: AI disabled means the request proceeds."""
    settings = parse_settings(valid_settings_dict())  # orchestrator_ai_enabled defaults False
    monkeypatch.setattr("ai_tech_lead.request_relevance.load_settings", lambda: settings)
    result = read_and_classify_request_node(graph_state(request="Fix the login bug"))
    assert result["atl_relevant"] is True


def test_route_after_read_and_classify_request_ends_when_not_relevant() -> None:
    state = graph_state(atl_relevant=False)
    assert route_after_read_and_classify_request(state) == "not relevant"


def test_route_after_read_and_classify_request_continues_when_relevant() -> None:
    state = graph_state(atl_relevant=True)
    assert route_after_read_and_classify_request(state) == "relevant"


def test_project_scope_decision_defaults_to_existing() -> None:
    result = project_scope_decision_node(graph_state())
    assert result["project_scope"] == "existing"


def test_check_code_look_need_node_records_decision(monkeypatch) -> None:
    def fake_check_code_look_need(request: str):
        assert request == "Fix the login bug"
        return SimpleNamespace(needs_code_look=True, reason="Touches existing auth code.")

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_code_look_need", fake_check_code_look_need
    )
    result = check_code_look_need_node(graph_state(bounded_request="Fix the login bug"))
    assert result["code_look_needed"] is True
    assert result["code_look_need_reason"] == "Touches existing auth code."


def test_codex_reads_code_node_is_read_only_and_stores_report(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=False)
    captured: dict[str, object] = {}
    progress_messages: list[str] = []

    def fake_load_settings():
        return settings

    def fake_run_coding_agent(*, agent_instruction, project_root, settings, **kwargs):
        captured["agent_instruction"] = agent_instruction
        captured["sandbox_override"] = kwargs.get("sandbox_override")

        class Result:
            stdout = "auth.py handles login; no changes needed to the schema."
            stderr = ""
            returncode = 0
            changed_files_delta: tuple[str, ...] = ()

        return Result()

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", fake_load_settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    state = graph_state(bounded_request="Fix the login bug")
    result = codex_reads_code_node(state, progress_callback=progress_messages.append)

    assert captured["sandbox_override"] == "read-only"
    assert "Fix the login bug" in str(captured["agent_instruction"])
    assert result["code_recon_report"] == "auth.py handles login; no changes needed to the schema."
    assert progress_messages == ["Codex finished looking at the code."]


def test_read_request_routes_to_project_scope_decision_not_resolve_context() -> None:
    """The merged read+classify node no longer goes straight to context resolution."""
    app = build_graph()
    edges = [(e.source, e.target) for e in app.get_graph().edges]
    targets_from_read_request = [t for s, t in edges if s == NodeName.READ_REQUEST]
    assert NodeName.PROJECT_SCOPE_DECISION in targets_from_read_request
    assert NodeName.END_NODE in targets_from_read_request

    targets_from_scope_decision = [
        t for s, t in edges if s == NodeName.PROJECT_SCOPE_DECISION
    ]
    assert targets_from_scope_decision == [NodeName.RESOLVE_CONTEXT]


def test_request_context_is_resolved_before_research(monkeypatch) -> None:
    state = graph_state(
        request="Code AF-052 for Agent Factory",
        target_project_context=TargetProjectContext(
            project_key="agent-factory",
            backlog_item=BacklogItemContext(
                project_key="agent-factory",
                spreadsheet_id="spreadsheet-a",
                sheet_name="Backlog",
                item_id="AF-052",
                title="Implement validation",
                body="Acceptance Criteria: bounded validation.",
            ),
        ).to_payload(),
    )

    resolved = resolve_context_node(state)
    state.update(resolved)

    assert route_after_resolve_context(state) == "context found"
    assert state["unresolved_references"] == []
    assert "bounded validation" in state["bounded_request"]


def test_unresolved_reference_routes_to_clarification_before_research() -> None:
    state = graph_state(
        request="Code AF-052 for Agent Factory",
        target_project_context=TargetProjectContext().to_payload(),
    )

    state.update(resolve_context_node(state))

    assert route_after_resolve_context(state) == "clarification needed"
    assert state["orchestrator_input_required"] is True
    assert state["orchestrator_input_question"] == (
        "What does AF-052 refer to, and where should I retrieve it from?"
    )


def test_route_after_resolve_context_retries_within_budget() -> None:
    state = graph_state(
        orchestrator_input_required=True,
        context_clarification_retry_count=CONTEXT_CLARIFICATION_MAX_RETRIES - 1,
    )
    assert route_after_resolve_context(state) == "clarification needed"


def test_route_after_resolve_context_fails_clearly_once_retry_limit_reached() -> None:
    state = graph_state(
        orchestrator_input_required=True,
        context_clarification_retry_count=CONTEXT_CLARIFICATION_MAX_RETRIES,
    )
    assert route_after_resolve_context(state) == "clarification limit reached"


def test_end_node_returns_restart_required() -> None:
    state = graph_state(restart_required=True)
    result = end_node(state)
    assert result["restart_required"] is True
    assert result["context_clarification_exhausted"] is False


def test_end_node_flags_exhausted_context_clarification() -> None:
    state = graph_state(
        orchestrator_input_required=True,
        context_clarification_retry_count=CONTEXT_CLARIFICATION_MAX_RETRIES,
    )
    result = end_node(state)
    assert result["context_clarification_exhausted"] is True


def test_request_plan_node_uses_resolved_target_project_root(monkeypatch, tmp_path) -> None:
    runtime_root = tmp_path / "ai-tech-lead"
    target_root = tmp_path / "agent-hub"
    runtime_root.mkdir()
    target_root.mkdir()
    settings = replace(
        parse_settings(valid_settings_dict()),
        project_root=str(runtime_root),
        execute_coding_agent=False,
    )
    captured: dict[str, Path] = {}

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **_replacements: "plan",
    )

    def fake_run_coding_agent(*, project_root, **_kwargs):
        captured["project_root"] = project_root
        return _FakeAgentResult(stdout="Plan", returncode=0)

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    request_plan_node(
        graph_state(
            resolved_project_root=str(target_root),
            target_project_context=TargetProjectContext(
                project_root=str(target_root),
                project_key="agent-hub",
            ).to_payload(),
        )
    )

    assert captured["project_root"] == target_root.resolve()


def test_instruction_assembly_uses_resolved_target_project_root(monkeypatch, tmp_path) -> None:
    runtime_root = tmp_path / "ai-tech-lead"
    target_root = tmp_path / "agent-factory"
    runtime_root.mkdir()
    target_root.mkdir()
    settings = replace(parse_settings(valid_settings_dict()), project_root=str(runtime_root))
    captured: dict[str, Path] = {}

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)

    def fake_build_agent_instruction(**kwargs):
        captured["project_root"] = kwargs["project_root"]
        return "instruction"

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_agent_instruction",
        fake_build_agent_instruction,
    )

    create_agent_instruction_node(
        graph_state(
            resolved_project_root=str(target_root),
            target_project_context=TargetProjectContext(
                project_root=str(target_root),
                project_key="agent-factory",
            ).to_payload(),
            brief="brief",
            formulated_task="task",
        )
    )

    assert captured["project_root"] == target_root.resolve()


def test_run_coding_agent_node_uses_resolved_target_project_root(monkeypatch, tmp_path) -> None:
    runtime_root = tmp_path / "ai-tech-lead"
    target_root = tmp_path / "agent-hub"
    runtime_root.mkdir()
    target_root.mkdir()
    settings = replace(
        parse_settings(valid_settings_dict()),
        project_root=str(runtime_root),
        execute_coding_agent=False,
    )
    captured: dict[str, Path] = {}

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)

    def fake_run_coding_agent(*, project_root, **_kwargs):
        captured["project_root"] = project_root
        return _FakeAgentResult(returncode=0)

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )

    run_coding_agent_node(
        graph_state(
            resolved_project_root=str(target_root),
            target_project_context=TargetProjectContext(
                project_root=str(target_root),
                project_key="agent-hub",
            ).to_payload(),
            agent_instruction="Do the task",
        )
    )

    assert captured["project_root"] == target_root.resolve()


def test_run_coding_agent_node_rejects_conflicting_project_root_override(
    monkeypatch, tmp_path
) -> None:
    runtime_root = tmp_path / "ai-tech-lead"
    target_root = tmp_path / "agent-hub"
    conflicting_root = tmp_path / "agent-factory"
    for path in (runtime_root, target_root, conflicting_root):
        path.mkdir()
    settings = replace(parse_settings(valid_settings_dict()), project_root=str(runtime_root))

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)

    import pytest

    with pytest.raises(ValueError, match="does not match"):
        run_coding_agent_node(
            graph_state(
                resolved_project_root=str(target_root),
                target_project_context=TargetProjectContext(
                    project_root=str(target_root),
                    project_key="agent-hub",
                ).to_payload(),
                agent_instruction="Do the task",
            ),
            project_root_override=str(conflicting_root),
        )


def test_request_plan_node_does_not_fall_back_to_runtime_root_when_boundary_root_missing(
    monkeypatch, tmp_path
) -> None:
    runtime_root = tmp_path / "ai-tech-lead"
    runtime_root.mkdir()
    settings = replace(
        parse_settings(valid_settings_dict()),
        project_root=str(runtime_root),
        execute_coding_agent=False,
    )

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **_replacements: "plan",
    )

    with pytest.raises(ValueError, match="Target project root is required"):
        request_plan_node(
            graph_state(
                target_project_context=TargetProjectContext(project_key="agent-hub").to_payload()
            )
        )
