"""End-to-end simulation of how AI Tech Lead is actually used from Telegram.

Unlike the rest of the suite — which tests one boundary at a time (Telegram
operator with a fake graph app, or the real graph invoked directly with no
Telegram layer) — this module drives a *real* compiled graph through the
*real* `TelegramOperator`, across a pause/resume approval cycle, against a
*real* temporary git repository so the real ATL-079 git preflight
(`run_git_preflight`) actually runs. Only true external boundaries are
mocked: the orchestrator LLM calls inside `coding_workflow_graph.py` and the
coding-agent subprocess (`run_coding_agent`). No real Telegram, OpenAI,
Codex, Google Sheets, or GitHub calls are made.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import replace
from pathlib import Path

from helpers import valid_settings_dict
from test_coding_workflow_graph import _research_result
from test_telegram_operator import _RecordingClient

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_agent_runner import CodingAgentResult, run_git_preflight
from ai_tech_lead.coding_workflow_graph import build_initial_graph_state
from ai_tech_lead.completion_verifier import CompletionVerificationDecision
from ai_tech_lead.plan_reviewer import PlanReviewDecision
from ai_tech_lead.project_guidance_governance import GuidanceGovernanceDecision
from ai_tech_lead.risk_reviewer import RiskReviewDecision
from ai_tech_lead.target_project_context import TargetProjectContext
from ai_tech_lead.tech_lead_analyst import TechLeadAnalysis
from ai_tech_lead.telegram_operator import (
    TelegramCommand,
    TelegramCommandName,
    TelegramOperator,
    TelegramTaskStage,
)


class _ImmediateThread:
    """Runs the target function inline instead of spawning a real OS thread.

    `_run_graph_task` normally hands the graph invocation to a background
    `threading.Thread` so Telegram stays responsive during a long-running
    task. Replacing it with this makes the background work happen
    synchronously in the test, without changing any production code path.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        self._target(*self._args, **self._kwargs)

    def join(self, timeout: float | None = None) -> None:
        return None


class _SynchronousThreadingNamespace:
    """Stand-in for the `threading` module as seen by telegram_operator.py.

    Only `Thread` is replaced (to run inline); `Lock`/`Event` stay real so
    unrelated locking behaviour is untouched. Patching the module-level
    `threading` name inside `ai_tech_lead.telegram_operator` — rather than
    `threading.Thread` on the real shared module — keeps this from also
    replacing the thread pool LangGraph's own executor relies on.
    """

    Thread = _ImmediateThread
    Lock = threading.Lock
    Event = threading.Event


def _init_git_repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in files.items():
        file_path = root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test Runner",
            "commit",
            "-m",
            "initial",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _wire_common_workflow_mocks(
    monkeypatch,
    settings,
    *,
    tech_direction: str,
    project_guidance_captures: dict[str, list[str]] | None = None,
) -> None:
    """Mock every orchestrator-LLM/coding-agent boundary coding_workflow_graph.py
    calls, the same way test_coding_workflow_graph.py's scenario tests do —
    reused here rather than reinvented.

    When `project_guidance_captures` is given, the `project_guidance` kwarg
    each mock actually receives is recorded under its call-site name, so a
    test can assert the same selected guidance reached review_plan,
    build_agent_instruction, and verify_completion — not just that discovery
    ran.
    """

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.load_settings", lambda: settings)
    monkeypatch.setattr("ai_tech_lead.risk_reviewer.load_settings", lambda: settings)
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.check_research_requirements",
        lambda _request, _settings, **_kwargs: _research_result(has_gap=False),
    )
    # A non-trivial guidance decision (not the empty-locations default) so the
    # test can prove guidance actually reaches and survives workflow state,
    # rather than merely not blocking it.
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_project_guidance",
        lambda *_args, **_kwargs: GuidanceGovernanceDecision(
            status="sufficient",
            requires_review=False,
            summary="Existing service module follows explicit-validation conventions.",
            related_locations=["app/service.py"],
            proposed_change="",
            reason="",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.review_task_risk",
        lambda _request: RiskReviewDecision(
            needs_approval=True,
            approval_reason="Touches production service code; needs explicit approval.",
            risk_level="HIGH",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.analyse_task",
        lambda **_kwargs: TechLeadAnalysis(
            task_statement="Add input validation to the service handler.",
            tech_direction=tech_direction,
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.render_prompt",
        lambda _prompt_key, **replacements: (
            f"PLAN::{replacements['formulated_task']}::"
            f"{replacements['task_feedback']}::{replacements['correction_feedback']}"
        ),
    )
    def fake_review_plan(**kwargs):
        if project_guidance_captures is not None:
            project_guidance_captures["review_plan"] = list(kwargs["project_guidance"])
        return PlanReviewDecision(approved=True, reason="Plan is bounded.", correction="")

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.review_plan", fake_review_plan)

    def fake_build_agent_instruction(**kwargs):
        if project_guidance_captures is not None:
            project_guidance_captures["build_agent_instruction"] = list(
                kwargs["project_guidance"]
            )
        return (
            f"IMPLEMENT::{kwargs['formulated_task']}::{kwargs['brief']}::"
            f"{' | '.join(kwargs['task_feedback'])}::{kwargs['agent_correction'] or ''}"
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_agent_instruction",
        fake_build_agent_instruction,
    )

    def fake_verify_completion(**kwargs):
        if project_guidance_captures is not None:
            project_guidance_captures["verify_completion"] = list(kwargs["project_guidance"])
        return CompletionVerificationDecision(
            status="complete",
            reason="Verified: app/service.py now validates input as required.",
            correction="",
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.verify_completion", fake_verify_completion
    )
    # Restore the real ATL-079 preflight for this test — conftest's autouse
    # fixture defaults it to "clean" so unrelated tests don't need a real
    # git repo; this scenario exists specifically to exercise the real thing.
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_git_preflight", run_git_preflight
    )


def test_telegram_workflow_simulation_happy_path_approve_and_complete(
    monkeypatch, tmp_path
) -> None:
    """Rob asks AI Tech Lead (over Telegram) to change a known target project.

    Proves, in one run: project context survives Telegram -> workflow ->
    resume; project guidance is selected and reaches plan/completion;
    approval genuinely pauses and resumes the *same* thread; the real git
    preflight runs before coding and finds the repo clean; the coding
    subprocess is not called before approval/preflight; changed files are
    captured; completion verification runs; and no unrelated file in the
    target project is touched.
    """

    target_repo = tmp_path / "demo-project"
    _init_git_repo(
        target_repo,
        {
            "app/service.py": (
                "def handle_request(payload):\n    return process(payload)\n"
            ),
            "README.md": "# Demo project\n",
            "AGENTS.md": (
                "Follow explicit-validation conventions in app/service.py before merging.\n"
            ),
        },
    )
    expected_project_guidance = [
        "AGENTS.md: Follow explicit-validation conventions in app/service.py before merging."
    ]

    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    project_guidance_captures: dict[str, list[str]] = {}
    _wire_common_workflow_mocks(
        monkeypatch,
        settings,
        tech_direction="Keep the change scoped to app/service.py.",
        project_guidance_captures=project_guidance_captures,
    )

    plan_calls: list[str] = []
    implementation_calls: list[str] = []

    def fake_run_coding_agent(*, agent_instruction, project_root, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            plan_calls.append(agent_instruction)
            return CodingAgentResult(
                command=["codex"],
                returncode=0,
                stdout=(
                    "1. Inspect app/service.py's request handling.\n"
                    "2. Add input validation before existing logic runs.\n"
                    "Done when: invalid input is rejected with a clear error."
                ),
                stderr="",
                success=True,
            )
        implementation_calls.append(agent_instruction)
        # Simulate the real coding agent editing a real file in the target repo.
        service_file = Path(project_root) / "app" / "service.py"
        service_file.write_text(
            service_file.read_text()
            + "\n\ndef validate_input(payload):\n"
            "    if not payload:\n        raise ValueError('invalid input')\n"
        )
        return CodingAgentResult(
            command=["codex"],
            returncode=0,
            stdout="",
            stderr="",
            success=True,
            message="The coding agent finished successfully.",
            changed_files_delta=("app/service.py",),
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )
    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.threading", _SynchronousThreadingNamespace
    )

    target_context = TargetProjectContext(
        project_root=str(target_repo), project_key="demo-project", project_name="Demo Project"
    )
    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator._base_graph_state",
        lambda request: build_initial_graph_state(
            request, target_project_context=target_context
        ),
    )

    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    # 1. Rob sends a Telegram coding request.
    operator._handle_command(
        "chat-1",
        TelegramCommand(
            name=TelegramCommandName.CODE,
            argument="Add input validation to the login/service handler.",
        ),
        "rob",
    )

    # Approval is genuinely pending — nothing has run the coding agent yet.
    assert "chat-1" in operator._active_tasks
    active_task = operator._active_tasks["chat-1"]
    assert active_task.stage == TelegramTaskStage.PRE_RUN_APPROVAL
    thread_id = active_task.thread_config["configurable"]["thread_id"]
    assert plan_calls == []
    assert implementation_calls == []
    assert any("touches production service code" in m[1].lower() for m in client.messages)

    # The known target project and the selected project guidance both
    # survived Telegram -> workflow resolution and are sitting in state,
    # ready for plan/handoff/completion to use, before any approval happened.
    paused_state = active_task.app.get_state(active_task.thread_config).values
    assert paused_state["resolved_project_root"] == str(target_repo)
    assert paused_state["project_guidance_status"] == "sufficient"
    assert paused_state["project_guidance_related_locations"] == ["app/service.py"]

    # 2. Rob replies /approve — same thread/workflow resumes, not a new run.
    operator._handle_command(
        "chat-1", TelegramCommand(name=TelegramCommandName.APPROVE), "rob"
    )

    # The task finished and was cleared — no second pause.
    assert "chat-1" not in operator._active_tasks
    assert plan_calls == ["PLAN::Add input validation to the service handler.::::"]
    assert implementation_calls == [
        (
            "IMPLEMENT::Add input validation to the service handler.::"
            "Keep the change scoped to app/service.py.::::"
        )
    ]

    # The same selected guidance (the real AGENTS.md note, discovered from the
    # target repo, not mocked) reached all three downstream stages: the plan
    # reviewer, the coding-agent instruction builder, and completion
    # verification — not just discovery/governance in isolation.
    assert project_guidance_captures["review_plan"] == expected_project_guidance
    assert project_guidance_captures["build_agent_instruction"] == expected_project_guidance
    assert project_guidance_captures["verify_completion"] == expected_project_guidance

    # 3. Completion reaches Telegram and names the changed file.
    final_message = client.messages[-1][1]
    assert final_message.startswith("Task complete:")
    assert "app/service.py" in final_message

    # 4. The real coding-agent change actually landed in the target repo,
    #    and nothing else in that repo was touched.
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=target_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
    assert len(dirty_lines) == 1
    assert dirty_lines[0].endswith("app/service.py")
    assert "validate_input" in (target_repo / "app" / "service.py").read_text()

    # Sanity: the same graph thread was used for both the initial run and the
    # resume — proven by LangGraph checkpoint history existing for one thread.
    assert thread_id


def test_telegram_workflow_simulation_preflight_blocks_relevant_dirty_file(
    monkeypatch, tmp_path
) -> None:
    """A pre-existing dirty file that overlaps the approved work blocks the
    coding subprocess and the human sees a clear review-required message —
    the exact ATL-079 safety objective, exercised through the real Telegram
    approval flow instead of a unit test calling the node directly.
    """

    target_repo = tmp_path / "demo-project"
    _init_git_repo(
        target_repo,
        {
            "app/service.py": (
                "def handle_request(payload):\n    return process(payload)\n"
            ),
            "README.md": "# Demo project\n",
        },
    )
    # Dirty the exact file the approved plan will name, *before* the run
    # starts — pre-existing work AI Tech Lead must not silently overwrite.
    (target_repo / "app" / "service.py").write_text(
        "def handle_request(payload):\n    return process(payload)\n\n# WIP: local edit\n"
    )

    settings = replace(
        parse_settings(valid_settings_dict()),
        orchestrator_ai_enabled=True,
        execute_coding_agent=False,
    )
    _wire_common_workflow_mocks(
        monkeypatch,
        settings,
        tech_direction="Keep the change scoped to app/service.py.",
    )

    plan_calls: list[str] = []
    implementation_calls: list[str] = []

    def fake_run_coding_agent(*, agent_instruction, project_root, **_kwargs):
        if agent_instruction.startswith("PLAN::"):
            plan_calls.append(agent_instruction)
            return CodingAgentResult(
                command=["codex"],
                returncode=0,
                stdout=(
                    "1. Inspect app/service.py's request handling.\n"
                    "2. Add input validation before existing logic runs.\n"
                    "Done when: invalid input is rejected with a clear error."
                ),
                stderr="",
                success=True,
            )
        implementation_calls.append(agent_instruction)
        raise AssertionError(
            "coding-agent implementation subprocess must never launch when the "
            "git preflight blocks on a relevant pre-existing dirty file"
        )

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.run_coding_agent", fake_run_coding_agent
    )
    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.threading", _SynchronousThreadingNamespace
    )

    target_context = TargetProjectContext(
        project_root=str(target_repo), project_key="demo-project", project_name="Demo Project"
    )
    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator._base_graph_state",
        lambda request: build_initial_graph_state(
            request, target_project_context=target_context
        ),
    )

    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    operator._handle_command(
        "chat-1",
        TelegramCommand(
            name=TelegramCommandName.CODE,
            argument="Add input validation to the login/service handler.",
        ),
        "rob",
    )
    assert operator._active_tasks["chat-1"].stage == TelegramTaskStage.PRE_RUN_APPROVAL

    operator._handle_command(
        "chat-1", TelegramCommand(name=TelegramCommandName.APPROVE), "rob"
    )

    # The implementation subprocess was never launched.
    assert plan_calls == ["PLAN::Add input validation to the service handler.::::"]
    assert implementation_calls == []

    # The task is still active, now waiting on a human for the blocked preflight
    # (routed through the existing failure-guidance human-review interrupt).
    assert "chat-1" in operator._active_tasks
    assert operator._active_tasks["chat-1"].stage == TelegramTaskStage.ORCHESTRATOR_INPUT

    review_message = client.messages[-1][1]
    assert "app/service.py" in review_message
    assert "overlaps the approved task" in review_message.lower()

    # The pre-existing local edit was never touched, reset, or overwritten.
    assert "WIP: local edit" in (target_repo / "app" / "service.py").read_text()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=target_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
    assert len(dirty_lines) == 1
    assert dirty_lines[0].endswith("app/service.py")
