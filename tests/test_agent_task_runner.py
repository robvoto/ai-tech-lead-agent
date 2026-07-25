"""Tests for agent_task_runner security model and I/O contract.

The workflow graph is always stubbed out — these tests verify the security
layer (input validation, project_root allowlist, execution gate, decision
resume mapping) without requiring LLM calls or a real LangGraph runtime.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from helpers import valid_settings_dict
from test_backlog_sheets_repository import (
    CREDENTIALS_PATH,
    HEADER,
    SHEET_NAME,
    FakeClient,
    FakeSpreadsheet,
    FakeWorksheet,
    _row,
)

import ai_tech_lead.backlog_sheets_repository as sheets_mod
from ai_tech_lead.agent_task_runner import (
    STATUS_FAILED,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_SUCCESS,
    STATUS_WAITING_DECISION,
    _build_supplied_context,
    _DecisionRejected,
    _execute_workflow,
    _map_decision_to_resume_payload,
    _map_state_to_output,
    _parse_decision,
    _validate_project_root,
    run_agent_task,
)
from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.progress_events import ProgressReporter, StdoutJsonlProgressSink
from ai_tech_lead.request_context import resolve_request_context
from ai_tech_lead.runtime_lock import RuntimeLockBusyError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_input(tmp_path: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_file = tmp_path / "input.json"
    output_file = tmp_path / "output.json"
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    return input_file, output_file


def _read_output(output_file: Path) -> dict[str, Any]:
    return json.loads(output_file.read_text(encoding="utf-8"))


def _success_output(instruction: str = "do it") -> dict[str, Any]:
    """Minimal output dict that _execute_workflow returns on success."""
    return {
        "request_id": "",
        "status": STATUS_SUCCESS,
        "summary": "Instruction generated.",
        "formulated_task": "",
        "brief": "",
        "coding_agent_instruction": instruction,
        "backend_used": "none",
        "execution_performed": False,
        "logs": [],
        "evidence": [],
        "next_action": "Submit instruction to coding backend.",
        "result_kind": "instruction_package",
        "pending_decision": None,
    }


def _waiting_decision_output(reason: str = "risky change") -> dict[str, Any]:
    """Minimal output dict that _execute_workflow returns when a decision is needed."""
    return {
        "request_id": "",
        "status": STATUS_WAITING_DECISION,
        "summary": f"Approval required: {reason}",
        "formulated_task": "",
        "brief": "",
        "coding_agent_instruction": "",
        "backend_used": "none",
        "execution_performed": False,
        "logs": [],
        "evidence": [],
        "next_action": "Resubmit request_id with a decision. Options: approve, cancel.",
        "result_kind": "decision_required",
        "pending_decision": {
            "thread_id": "subprocess-req",
            "kind": "approval",
            "prompt": f"Approval required: {reason}",
            "options": [
                {"name": "approve"},
                {"name": "request_changes", "needs_text": True},
                {"name": "ask_question", "needs_text": True},
                {"name": "cancel"},
            ],
        },
    }


def _make_fake_workflow(monkeypatch: pytest.MonkeyPatch, output: dict[str, Any]) -> list[dict]:
    """Stub _execute_workflow to return a fixed output dict without running LangGraph.

    ``output`` must be a properly structured output dict (what the real _execute_workflow
    returns after calling _map_state_to_output), not raw graph state.
    """
    calls: list[dict] = []

    def fake_execute(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return output

    monkeypatch.setattr("ai_tech_lead.agent_task_runner._execute_workflow", fake_execute)
    return calls


def _stub_settings(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any] | None = None
) -> None:
    """Stub load_settings to return a known settings object without reading disk."""
    raw = valid_settings_dict()
    settings = parse_settings(raw)
    if overrides:
        settings = replace(settings, **overrides)

    monkeypatch.setattr("ai_tech_lead.agent_task_runner.load_settings", lambda: settings)


def _execution_success_output(summary: str = "Task completed successfully.") -> dict[str, Any]:
    """Minimal output dict for a real (executed, not just formulated) success."""
    return {
        "request_id": "",
        "status": STATUS_SUCCESS,
        "summary": summary,
        "formulated_task": "",
        "brief": "",
        "coding_agent_instruction": "implement it",
        "backend_used": "codex",
        "execution_performed": True,
        "logs": [],
        "evidence": [],
        "next_action": "Review output.",
        "result_kind": "execution_result",
        "pending_decision": None,
    }


def _install_fake_sheets_client(monkeypatch: pytest.MonkeyPatch, spreadsheet_id: str, row) -> None:
    """Inject a fake gspread client so backlog_reference resolution hits no network."""
    worksheet = FakeWorksheet([HEADER, row])
    client = FakeClient({spreadsheet_id: FakeSpreadsheet({SHEET_NAME: worksheet})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_missing_task_returns_failed_without_running_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(tmp_path, {"request_id": "r1"})
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "task" in result["summary"].lower()
    assert result["agent_manifest"]["agent_id"] == "ai-tech-lead"
    assert result["agent_manifest"]["manifest_command"] == "uv run python -m ai_tech_lead manifest"
    assert calls == [], "workflow must not be invoked when task is missing"


def test_empty_task_returns_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(tmp_path, {"task": "   "})
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert calls == []


def test_unreadable_input_file_returns_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)
    output_file = tmp_path / "output.json"

    rc = run_agent_task(tmp_path / "nonexistent.json", output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED


def test_decision_without_request_id_returns_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path, {"decision": {"option": "approve"}}
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "request_id" in result["summary"].lower()
    assert calls == []


def test_decision_without_task_does_not_require_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resuming with a decision must not require a task field."""
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-resume", "decision": {"option": "approve"}},
    )
    rc = run_agent_task(input_file, output_file)

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["decision"].option == "approve"


# ---------------------------------------------------------------------------
# project_root allowlist
# ---------------------------------------------------------------------------


class TestValidateProjectRoot:
    def _settings(self, allowed_roots: list[str]) -> Any:
        settings = parse_settings(valid_settings_dict())
        return replace(settings, allowed_project_roots=allowed_roots)

    def test_none_input_returns_none(self) -> None:
        settings = self._settings(["/allowed/path"])
        assert _validate_project_root(None, settings) is None

    def test_allowlisted_path_is_accepted(self, tmp_path: Path) -> None:
        root = str(tmp_path)
        settings = self._settings([root])
        assert _validate_project_root(root, settings) == str(Path(root).resolve())

    def test_non_allowlisted_path_returns_none(self, tmp_path: Path) -> None:
        settings = self._settings(["/some/other/path"])
        result = _validate_project_root(str(tmp_path), settings)
        assert result is None

    def test_path_is_resolved_before_comparison(self, tmp_path: Path) -> None:
        # Trailing slash, symlink-style: both sides resolve to the same absolute path
        root = str(tmp_path)
        settings = self._settings([root])
        result = _validate_project_root(root + "/", settings)
        assert result == str(Path(root).resolve())

    def test_empty_allowlist_rejects_all_external_roots(self, tmp_path: Path) -> None:
        settings = self._settings([])
        result = _validate_project_root(str(tmp_path), settings)
        assert result is None


def test_project_root_not_in_allowlist_returns_failed_without_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"allowed_project_roots": ["/some/other/path"]})

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "project_root": "/some/evil/path"},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "allowlist" in result["summary"].lower()
    assert calls == [], "workflow must not run when project_root is rejected"


def test_project_root_in_allowlist_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = str(tmp_path / "allowed-project")
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"allowed_project_roots": [allowed_root]})

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "project_root": allowed_root},
    )
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["project_root"] == str(Path(allowed_root).resolve())


def test_no_project_root_passes_none_to_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(tmp_path, {"task": "fix bug"})
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["project_root"] is None


# ---------------------------------------------------------------------------
# Execution gate: local settings decide, not the caller
# ---------------------------------------------------------------------------


def test_execute_mode_disabled_in_settings_is_not_overridden_by_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller requests execute, but settings.execute_coding_agent=False → no execution."""
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"execute_coding_agent": False})

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "execution_mode": "execute", "requires_human_approval": False},
    )
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["execute_coding_agent"] is False


def test_execute_mode_enabled_in_settings_allows_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"execute_coding_agent": True})

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "execution_mode": "execute"},
    )
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["execute_coding_agent"] is True


def test_missing_execution_mode_defaults_to_instruction_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"execute_coding_agent": True})

    input_file, output_file = _write_input(tmp_path, {"task": "fix bug"})
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["execute_coding_agent"] is False


def test_unknown_execution_mode_returns_failed_without_running_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "execution_mode": "do_magic"},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "execution_mode" in result["summary"].lower()
    assert calls == []


def test_requires_human_approval_from_caller_is_ignored_for_execution_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller sends requires_human_approval=false — this must not bypass the settings gate."""
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"execute_coding_agent": False})

    input_file, output_file = _write_input(
        tmp_path,
        {
            "task": "fix bug",
            "execution_mode": "execute",
            "requires_human_approval": False,  # caller tries to bypass
        },
    )
    run_agent_task(input_file, output_file)

    # execution gate is still False because settings say so
    assert calls[0]["execute_coding_agent"] is False


def test_instruction_only_mode_disables_execution_regardless_of_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch, {"execute_coding_agent": True})

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "execution_mode": "instruction_only"},
    )
    run_agent_task(input_file, output_file)

    assert calls[0]["execute_coding_agent"] is False


def test_duplicate_request_id_returns_failed_without_running_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)
    monkeypatch.setattr(
        "ai_tech_lead.agent_task_runner.acquire_request_run_lock",
        lambda _request_id: (_ for _ in ()).throw(
            RuntimeLockBusyError("Runtime lock busy for request-run: req-dup")
        ),
    )

    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-dup", "task": "fix bug"},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "request-run" in result["summary"]
    assert calls == []


# ---------------------------------------------------------------------------
# Decision parsing and resume mapping
# ---------------------------------------------------------------------------


def test_parse_decision_returns_none_when_absent() -> None:
    assert _parse_decision(None) is None


def test_parse_decision_requires_option() -> None:
    with pytest.raises(_DecisionRejected, match="option"):
        _parse_decision({"text": "no option here"})


def test_parse_decision_rejects_non_dict() -> None:
    with pytest.raises(_DecisionRejected):
        _parse_decision("approve")


def test_parse_decision_reads_fields() -> None:
    decision = _parse_decision({"option": "approve", "text": "", "actor": "agent-x"})
    assert decision is not None
    assert decision.option == "approve"
    assert decision.actor == "agent-x"


def test_map_decision_rejects_option_not_offered_for_kind() -> None:
    decision = _parse_decision({"option": "self_destruct"})
    with pytest.raises(_DecisionRejected, match="not a valid option"):
        _map_decision_to_resume_payload("approval", decision)


def test_map_decision_requires_text_when_option_needs_it() -> None:
    decision = _parse_decision({"option": "request_changes"})
    with pytest.raises(_DecisionRejected, match="text is required"):
        _map_decision_to_resume_payload("approval", decision)


def test_map_decision_approve_maps_to_graph_payload() -> None:
    decision = _parse_decision({"option": "approve", "actor": "agent-x"})
    payload = _map_decision_to_resume_payload("approval", decision)
    assert payload == {"action": "approve", "approved_by": "agent-x"}


def test_map_decision_approve_defaults_actor() -> None:
    decision = _parse_decision({"option": "approve"})
    payload = _map_decision_to_resume_payload("approval", decision)
    assert payload == {"action": "approve", "approved_by": "agent-caller"}


def test_map_decision_request_changes_carries_feedback() -> None:
    decision = _parse_decision({"option": "request_changes", "text": "Docs only please."})
    payload = _map_decision_to_resume_payload("approval", decision)
    assert payload == {"action": "request_changes", "feedback": "Docs only please."}


def test_map_decision_ask_question_carries_question() -> None:
    decision = _parse_decision({"option": "ask_question", "text": "Which files change?"})
    payload = _map_decision_to_resume_payload("approval", decision)
    assert payload == {"action": "ask_question", "question": "Which files change?"}


def test_map_decision_cancel_maps_to_graph_payload() -> None:
    decision = _parse_decision({"option": "cancel"})
    payload = _map_decision_to_resume_payload("approval", decision)
    assert payload == {"action": "cancel"}


def test_map_decision_plan_guidance_is_plain_text() -> None:
    decision = _parse_decision({"option": "answer", "text": "Keep it to one file."})
    payload = _map_decision_to_resume_payload("plan_guidance", decision)
    assert payload == "Keep it to one file."


def test_map_decision_failure_guidance_is_plain_text() -> None:
    decision = _parse_decision({"option": "answer", "text": "Retry with a narrower fix."})
    payload = _map_decision_to_resume_payload("failure_guidance", decision)
    assert payload == "Retry with a narrower fix."


def test_map_decision_research_approval_approve() -> None:
    decision = _parse_decision({"option": "approve"})
    payload = _map_decision_to_resume_payload("research_approval", decision)
    assert payload == {"approved": True}


def test_map_decision_research_approval_cancel() -> None:
    decision = _parse_decision({"option": "cancel"})
    payload = _map_decision_to_resume_payload("research_approval", decision)
    assert payload == {"approved": False}


def test_map_decision_completion_verification_confirm_complete() -> None:
    decision = _parse_decision({"option": "confirm_complete"})
    payload = _map_decision_to_resume_payload("completion_verification", decision)
    assert payload == {"decision": "confirm_complete"}


def test_map_decision_completion_verification_reject_carries_text() -> None:
    decision = _parse_decision({"option": "reject", "text": "The button still crashes."})
    payload = _map_decision_to_resume_payload("completion_verification", decision)
    assert payload == {"decision": "reject", "text": "The button still crashes."}


def test_map_decision_completion_verification_reject_requires_text() -> None:
    decision = _parse_decision({"option": "reject"})
    with pytest.raises(_DecisionRejected, match="text is required"):
        _map_decision_to_resume_payload("completion_verification", decision)


# ---------------------------------------------------------------------------
# Resuming a paused conversation end to end (fake graph)
# ---------------------------------------------------------------------------


class _FakeInterrupt:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value


class _FakeTask:
    def __init__(self, value: dict[str, Any]) -> None:
        self.interrupts = (_FakeInterrupt(value),)


class _FakeSnapshot:
    def __init__(self, value: dict[str, Any] | None, *, paused: bool = True) -> None:
        self.next = ("paused",) if paused else ()
        self.tasks = (_FakeTask(value),) if value is not None else ()


class _FakeResumableGraph:
    """Fake graph that remembers whether it was resumed vs freshly invoked."""

    def __init__(self, pending_value: dict[str, Any]) -> None:
        self._pending_value = pending_value
        self.invoke_calls: list[Any] = []
        self.resumed = False

    def get_state(self, _config: dict[str, Any]) -> _FakeSnapshot:
        if self.resumed:
            return _FakeSnapshot(None, paused=False)
        return _FakeSnapshot(self._pending_value, paused=True)

    def invoke(self, value: Any, *, config: dict[str, Any]) -> dict[str, Any]:
        self.invoke_calls.append(value)
        from langgraph.types import Command

        if isinstance(value, Command):
            self.resumed = True
            return {
                "agent_instruction": "",
                "formulated_task": "",
                "brief": "",
                "orchestrator_input_required": False,
                "orchestrator_input_question": "",
                "coding_agent_success": None,
                "coding_agent_result": "",
                "restart_required": False,
                "task_feedback": [],
                "research_source_titles": [],
                "coding_agent_performed_by": "none",
            }
        return {
            "agent_instruction": "",
            "formulated_task": "",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": None,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
            "coding_agent_performed_by": "none",
        }


def test_execute_workflow_resumes_with_command_when_decision_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _FakeResumableGraph(
        {"kind": "approval", "reason": "risky", "formulated_task": "Do the thing"}
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_graph", lambda **_kwargs: graph
    )

    decision = _parse_decision({"option": "approve", "actor": "agent-x"})
    _execute_workflow(
        request_id="req-resume-graph",
        task="",
        execute_coding_agent=False,
        project_root=None,
        decision=decision,
    )

    from langgraph.types import Command

    assert len(graph.invoke_calls) == 1
    resumed_command = graph.invoke_calls[0]
    assert isinstance(resumed_command, Command)
    assert resumed_command.resume == {"action": "approve", "approved_by": "agent-x"}


def test_execute_workflow_rejects_decision_when_nothing_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoPendingGraph:
        def get_state(self, _config: dict[str, Any]) -> _FakeSnapshot:
            return _FakeSnapshot(None, paused=False)

        def invoke(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("invoke should not be called")

    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_graph",
        lambda **_kwargs: _NoPendingGraph(),
    )

    decision = _parse_decision({"option": "approve"})
    with pytest.raises(_DecisionRejected, match="No paused decision"):
        _execute_workflow(
            request_id="req-nothing-pending",
            task="",
            execute_coding_agent=False,
            project_root=None,
            decision=decision,
        )


def test_decision_resume_flows_through_run_agent_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeResumableGraph({"kind": "approval", "reason": "risky", "formulated_task": ""})
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_graph", lambda **_kwargs: graph
    )
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-full-resume",
            "decision": {"option": "cancel"},
        },
    )
    rc = run_agent_task(input_file, output_file)

    assert rc == 1  # workflow output here has no agent_instruction -> terminal failure shape
    from langgraph.types import Command

    assert len(graph.invoke_calls) == 1
    assert isinstance(graph.invoke_calls[0], Command)
    assert graph.invoke_calls[0].resume == {"action": "cancel"}


def test_invalid_decision_option_returns_failed_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeResumableGraph({"kind": "approval", "reason": "risky", "formulated_task": ""})
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_graph", lambda **_kwargs: graph
    )
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-bad-option", "decision": {"option": "yolo"}},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "not a valid option" in result["summary"]
    assert graph.invoke_calls == []


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_successful_response_includes_all_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _success_output("implement the fix")
    output.update(
        {
            "request_id": "test-output-shape",
            "formulated_task": "Fix the login bug",
            "brief": "Tech lead analysis...",
            "logs": ["step 1", "step 2"],
            "evidence": ["LangGraph docs"],
        }
    )
    _make_fake_workflow(monkeypatch, output)
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(tmp_path, {"task": "fix login bug"})
    run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    required_keys = {
        "request_id",
        "status",
        "summary",
        "formulated_task",
        "brief",
        "coding_agent_instruction",
        "backend_used",
        "execution_performed",
        "logs",
        "evidence",
        "next_action",
        "result_kind",
        "pending_decision",
        "agent_manifest",
    }
    assert required_keys.issubset(result.keys())
    assert result["status"] == STATUS_SUCCESS
    assert result["agent_manifest"]["agent_id"] == "ai-tech-lead"
    assert result["agent_manifest"]["manifest_command"] == "uv run python -m ai_tech_lead manifest"


def test_map_state_to_output_maps_plan_interrupt_to_waiting_decision() -> None:
    result = _map_state_to_output(
        "req-plan",
        {
            "agent_instruction": "",
            "formulated_task": "Fix the task",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": False,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        False,
        state_snapshot=_FakeSnapshot(
            {
                "kind": "plan_guidance",
                "reason": "Plan reviewer unavailable",
                "plan_text": "",
                "rejection_count": 0,
            }
        ),
        thread_id="subprocess-req-plan",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert "Plan reviewer unavailable" in result["summary"]
    assert result["result_kind"] == "decision_required"
    assert result["pending_decision"]["kind"] == "plan_guidance"
    assert result["pending_decision"]["options"] == [{"name": "answer", "needs_text": True}]
    assert result["pending_decision"]["thread_id"] == "subprocess-req-plan"


def test_map_state_to_output_maps_failure_interrupt_to_waiting_decision() -> None:
    result = _map_state_to_output(
        "req-failure",
        {
            "agent_instruction": "",
            "formulated_task": "Fix the task",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": False,
            "coding_agent_result": "Tests failed",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        False,
        state_snapshot=_FakeSnapshot(
            {
                "kind": "failure_guidance",
                "coding_agent_result": "Tests failed in CI",
                "retry_count": 2,
            }
        ),
        thread_id="subprocess-req-failure",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert "Tests failed in CI" in result["summary"]
    assert result["result_kind"] == "decision_required"
    assert result["pending_decision"]["kind"] == "failure_guidance"


def test_map_state_to_output_maps_approval_interrupt_with_four_options() -> None:
    result = _map_state_to_output(
        "req-approval",
        {
            "agent_instruction": "",
            "formulated_task": "Update docs",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": False,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        False,
        state_snapshot=_FakeSnapshot(
            {"kind": "approval", "reason": "High risk task.", "formulated_task": "Update docs"}
        ),
        thread_id="subprocess-req-approval",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    option_names = {opt["name"] for opt in result["pending_decision"]["options"]}
    assert option_names == {"approve", "request_changes", "ask_question", "cancel"}


def test_map_state_to_output_maps_research_approval() -> None:
    result = _map_state_to_output(
        "req-research",
        {
            "agent_instruction": "",
            "formulated_task": "",
            "brief": "",
            "orchestrator_input_required": True,
            "orchestrator_input_question": "Should I fetch approved docs?",
            "coding_agent_success": False,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        False,
        state_snapshot=_FakeSnapshot(
            {"kind": "research_approval", "question": "Should I fetch approved docs?"}
        ),
        thread_id="subprocess-req-research",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert result["pending_decision"]["kind"] == "research_approval"
    option_names = {opt["name"] for opt in result["pending_decision"]["options"]}
    assert option_names == {"approve", "cancel"}


def test_map_state_to_output_maps_completion_verification_interrupt() -> None:
    result = _map_state_to_output(
        "req-verify",
        {
            "agent_instruction": "implement it",
            "formulated_task": "Add a logout button",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": True,
            "coding_agent_result": "Implemented and ran pytest.",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
            "verification_status": "human_verification_required",
            "verification_reason": "Requires a visual check of the button placement.",
        },
        False,
        state_snapshot=_FakeSnapshot(
            {
                "kind": "completion_verification",
                "reason": "Requires a visual check of the button placement.",
                "coding_agent_result": "Implemented and ran pytest.",
                "changed_files": ["src/app/settings.py"],
            }
        ),
        thread_id="subprocess-req-verify",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert result["result_kind"] == "decision_required"
    assert result["pending_decision"]["kind"] == "completion_verification"
    option_names = {opt["name"] for opt in result["pending_decision"]["options"]}
    assert option_names == {"confirm_complete", "reject"}
    assert "visual check" in result["pending_decision"]["prompt"]


def test_map_state_to_output_reports_failed_when_verification_fails_after_success() -> None:
    """A coding agent that exits cleanly but fails AI Tech Lead verification is not a success."""
    result = _map_state_to_output(
        "req-verify-failed",
        {
            "agent_instruction": "implement it",
            "formulated_task": "Add a logout button",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": True,
            "coding_agent_result": "Implemented.",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
            "verification_status": "failed",
            "verification_reason": "The logout button does not sign the user out.",
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-verify-failed",
    )

    assert result["status"] == STATUS_FAILED
    assert result["result_kind"] == "terminal_failure"
    assert "does not sign the user out" in result["summary"]


def test_map_state_to_output_reports_success_when_verification_complete() -> None:
    result = _map_state_to_output(
        "req-verify-complete",
        {
            "agent_instruction": "implement it",
            "formulated_task": "Add a logout button",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": True,
            "coding_agent_result": "Implemented.",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
            "verification_status": "complete",
            "verification_reason": "Acceptance criteria satisfied.",
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-verify-complete",
    )

    assert result["status"] == STATUS_SUCCESS
    assert result["result_kind"] == "execution_result"


def test_map_state_to_output_dead_end_clarification_has_no_pending_decision() -> None:
    """An unresolved reference ends the graph — there is nothing to resume."""
    result = _map_state_to_output(
        "req-clarify",
        {
            "agent_instruction": "",
            "formulated_task": "",
            "brief": "",
            "orchestrator_input_required": True,
            "orchestrator_input_question": "What does AF-052 refer to?",
            "coding_agent_success": False,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-clarify",
    )

    assert result["status"] == STATUS_NEEDS_CLARIFICATION
    assert result["pending_decision"] is None
    assert result["result_kind"] == "clarification_request"


def test_map_state_to_output_uses_failed_for_terminal_agent_failure() -> None:
    result = _map_state_to_output(
        "req-failed",
        {
            "agent_instruction": "run this",
            "formulated_task": "Fix the task",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": False,
            "coding_agent_result": "command exited 1",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        True,
    )

    assert result["status"] == STATUS_FAILED
    assert "Coding agent failed" in result["summary"]
    assert result["result_kind"] == "terminal_failure"


# ---------------------------------------------------------------------------
# Hub progress stream
# ---------------------------------------------------------------------------


def _captured_progress_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_run_agent_task_streams_start_and_completion_when_run_id_is_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_fake_workflow(monkeypatch, _success_output("implement it"))
    _stub_settings(monkeypatch)
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-progress-1",
            "run_id": "run-progress-1",
            "task": "fix the bug",
        },
    )

    rc = run_agent_task(input_file, output_file)

    assert rc == 0
    events = _captured_progress_events(capsys)
    assert [(event["event_type"], event["phase"]) for event in events] == [
        ("start", "starting"),
        ("completed", "completed"),
    ]
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["run_id"] == "run-progress-1" for event in events)
    assert all(event["request_id"] == "req-progress-1" for event in events)
    assert "progress" not in _read_output(output_file)


def test_run_agent_task_without_run_id_keeps_stdout_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)
    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-no-progress", "task": "fix the bug"},
    )

    assert run_agent_task(input_file, output_file) == 0

    assert capsys.readouterr().out == ""
    assert _read_output(output_file)["status"] == STATUS_SUCCESS


def test_run_agent_task_streams_failure_when_workflow_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_settings(monkeypatch)

    def fail_workflow(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("workflow exploded")

    monkeypatch.setattr("ai_tech_lead.agent_task_runner._execute_workflow", fail_workflow)
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-progress-failure",
            "run_id": "run-progress-failure",
            "task": "fix the bug",
        },
    )

    assert run_agent_task(input_file, output_file) == 1

    events = _captured_progress_events(capsys)
    assert [event["event_type"] for event in events] == ["start", "failure"]
    assert events[-1]["human_summary"] == "AI Tech Lead could not complete the task."
    assert _read_output(output_file)["status"] == STATUS_FAILED


def test_run_agent_task_streams_cancelled_when_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_settings(monkeypatch)

    def cancel_workflow(**_kwargs: Any) -> dict[str, Any]:
        raise KeyboardInterrupt

    monkeypatch.setattr("ai_tech_lead.agent_task_runner._execute_workflow", cancel_workflow)
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-progress-cancel",
            "run_id": "run-progress-cancel",
            "task": "fix the bug",
        },
    )

    with pytest.raises(KeyboardInterrupt):
        run_agent_task(input_file, output_file)

    events = _captured_progress_events(capsys)
    assert [event["event_type"] for event in events] == ["start", "warning"]
    assert events[-1]["phase"] == "cancelled"
    assert not output_file.exists()


def test_execute_workflow_translates_existing_graph_progress_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(
            run_id="run-graph-progress",
            request_id="req-graph-progress",
            stream=stream,
        ),
        heartbeat_interval_seconds=0,
    )

    class _Snapshot:
        next: tuple[str, ...] = ()
        tasks: tuple[Any, ...] = ()

    class _Graph:
        def __init__(self, callback: Any) -> None:
            self._callback = callback

        def invoke(self, _state: dict[str, Any], *, config: dict[str, Any]) -> dict[str, Any]:
            assert config["configurable"]["thread_id"] == "subprocess-req-graph-progress"
            assert callable(self._callback)
            self._callback("Requesting implementation plan from coding agent...")
            self._callback("Plan received. Reviewing...")
            self._callback("Running coding agent (codex). This may take several minutes...")
            return {
                "agent_instruction": "implement it",
                "formulated_task": "Implement the change",
                "brief": "bounded brief",
                "orchestrator_input_required": False,
                "orchestrator_input_question": "",
                "coding_agent_success": None,
                "coding_agent_result": "",
                "restart_required": False,
                "task_feedback": [],
                "research_source_titles": [],
                "coding_agent_performed_by": "none",
            }

        def get_state(self, _config: dict[str, Any]) -> _Snapshot:
            return _Snapshot()

    def fake_build_graph(**kwargs: Any) -> _Graph:
        return _Graph(kwargs["coding_agent_progress_callback"])

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.build_graph", fake_build_graph)

    result = _execute_workflow(
        request_id="req-graph-progress",
        task="Implement the change",
        execute_coding_agent=False,
        project_root=None,
        progress_reporter=reporter,
    )

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["phase"] for event in events] == [
        "analysing",
        "planning",
        "reviewing_plan",
        "coding",
        "finalising",
    ]
    assert result["status"] == STATUS_SUCCESS
    assert result["result_kind"] == "instruction_package"


# ---------------------------------------------------------------------------
# backlog_reference (Agent Hub-facing structured backlog identity)
# ---------------------------------------------------------------------------


def test_malformed_backlog_reference_is_rejected_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)
    _make_fake_workflow(monkeypatch, _execution_success_output())
    input_file, output_file = _write_input(
        tmp_path,
        {"task": "do the thing", "backlog_reference": {"item_id": "ATL-001"}},
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 1
    result = _read_output(output_file)
    assert result["status"] == STATUS_FAILED
    assert "backlog_reference could not be resolved" in result["summary"]
    assert result["backlog_sync_status"] == "not_applicable"


def test_backlog_reference_unknown_item_id_is_rejected_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)
    _make_fake_workflow(monkeypatch, _execution_success_output())
    _install_fake_sheets_client(
        monkeypatch, "spreadsheet-a", _row("ATL-001", "Existing item")
    )
    input_file, output_file = _write_input(
        tmp_path,
        {
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-999",
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 1
    result = _read_output(output_file)
    assert result["status"] == STATUS_FAILED
    assert "backlog_reference could not be resolved" in result["summary"]


def test_backlog_reference_syncs_on_real_execution_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)
    _make_fake_workflow(monkeypatch, _execution_success_output("Implemented the change."))
    _install_fake_sheets_client(
        monkeypatch, "spreadsheet-a", _row("ATL-001", "Existing item", status="Backlog")
    )
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-hub-1",
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-001",
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["status"] == STATUS_SUCCESS
    assert result["backlog_sync_status"] == "synced"

    from ai_tech_lead.backlog_reference import BacklogReference
    from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository

    repository = SheetsBacklogRepository(
        BacklogReference("ai-tech-lead", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    assert repository.get_item("ATL-001").status.value == "Done"


def test_backlog_reference_not_applicable_when_only_instruction_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An instruction_package (no real execution) must not touch the Sheet."""
    _stub_settings(monkeypatch)
    _make_fake_workflow(monkeypatch, _success_output())
    _install_fake_sheets_client(
        monkeypatch, "spreadsheet-a", _row("ATL-001", "Existing item", status="Backlog")
    )
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-hub-2",
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-001",
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["backlog_sync_status"] == "not_applicable"

    from ai_tech_lead.backlog_reference import BacklogReference
    from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository

    repository = SheetsBacklogRepository(
        BacklogReference("ai-tech-lead", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    assert repository.get_item("ATL-001").status.value == "Backlog"


def test_backlog_reference_supports_dynamic_second_spreadsheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spreadsheet AI Tech Lead has never seen before works with no code change."""
    _stub_settings(monkeypatch)
    worksheet_hub = FakeWorksheet(
        [HEADER, _row("HUB-001", "Hub item", status="Backlog")]
    )
    client = FakeClient({"spreadsheet-hub": FakeSpreadsheet({SHEET_NAME: worksheet_hub})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    _make_fake_workflow(monkeypatch, _execution_success_output())
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-hub-3",
            "task": "do the thing",
            "backlog_reference": {
                "project_key": "agent-hub",
                "spreadsheet_id": "spreadsheet-hub",
                "sheet_name": SHEET_NAME,
                "item_id": "HUB-001",
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["backlog_sync_status"] == "synced"


def test_backlog_reference_reuses_existing_snapshot_on_resubmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resubmitted request_id continues its existing snapshot, not a fresh one."""
    from ai_tech_lead.backlog_runtime_store import BacklogRuntimeStore

    _stub_settings(monkeypatch)
    _install_fake_sheets_client(
        monkeypatch, "spreadsheet-a", _row("ATL-001", "Existing item", status="Backlog")
    )
    _make_fake_workflow(monkeypatch, _waiting_decision_output())
    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-resume-1",
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-001",
            },
        },
    )

    run_agent_task(input_file, output_file)
    store = BacklogRuntimeStore()
    first_snapshot = store.get_snapshot("req-resume-1")
    assert first_snapshot is not None

    # Resubmit the same request_id (e.g. after providing clarification).
    run_agent_task(input_file, output_file)
    second_snapshot = store.get_snapshot("req-resume-1")

    assert second_snapshot is not None
    assert second_snapshot.request_id == first_snapshot.request_id
    assert second_snapshot.item_id == "ATL-001"


# ---------------------------------------------------------------------------
# Hub reference normalization (universal `references` vs legacy `resource_references`)
# ---------------------------------------------------------------------------


def test_hub_references_are_normalized_into_resource_references() -> None:
    context = _build_supplied_context(
        task_input={"references": ["AF-052", "docs/runbook.md"]},
        project_root=None,
        backlog_reference=None,
        source_record=None,
    )

    assert context["resource_references"] == [
        {"id": "AF-052"},
        {"id": "docs/runbook.md"},
    ]


def test_legacy_resource_references_still_work_alongside_hub_references() -> None:
    context = _build_supplied_context(
        task_input={
            "resource_references": [{"item_id": "AF-052", "title": "legacy title"}],
            "references": ["AF-052", "AH-010"],
        },
        project_root=None,
        backlog_reference=None,
        source_record=None,
    )

    # The legacy dict for AF-052 is kept as-is (not overwritten by Hub's bare string);
    # AH-010 is added from Hub's references since no legacy entry supplied it.
    assert context["resource_references"] == [
        {"item_id": "AF-052", "title": "legacy title"},
        {"id": "AH-010"},
    ]


def test_hub_style_references_reach_the_existing_context_resolver() -> None:
    """A Hub `references` payload must resolve through resolve_request_context exactly
    like a legacy `resource_references` payload would — the AI Tech Lead boundary only
    normalizes shape; request_context.py remains the sole place resolution happens."""
    supplied_context = _build_supplied_context(
        task_input={"references": ["AF-052"]},
        project_root=None,
        backlog_reference=None,
        source_record=None,
    )

    result = resolve_request_context(
        "Code AF-052 for Agent Factory", supplied_context=supplied_context
    )

    assert result["unresolved_references"] == []
    assert result["resolved_resource_references"] == ["AF-052"]
    assert result["clarification_question"] == ""


def test_hub_reference_not_matching_request_text_stays_unresolved() -> None:
    """A Hub reference that doesn't correspond to anything detected in the request text
    must not mask an unrelated unresolved reference — clarification still fires for it."""
    supplied_context = _build_supplied_context(
        task_input={"references": ["docs/runbook.md"]},
        project_root=None,
        backlog_reference=None,
        source_record=None,
    )

    result = resolve_request_context(
        "Code AF-052 for Agent Factory", supplied_context=supplied_context
    )

    assert result["unresolved_references"] == ["AF-052"]
    assert result["clarification_question"] == (
        "What does AF-052 refer to, and where should I retrieve it from?"
    )
