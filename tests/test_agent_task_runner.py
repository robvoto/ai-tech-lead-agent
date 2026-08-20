"""Tests for agent_task_runner security model and I/O contract.

The workflow graph is always stubbed out — these tests verify the security
layer (input validation, project_root authorization, execution gate, decision
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
from requests.exceptions import ConnectionError as RequestsConnectionError
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
    _build_target_project_context,
    _DecisionRejected,
    _execute_workflow,
    _map_decision_to_resume_payload,
    _map_state_to_output,
    _parse_decision,
    _resolve_backlog_project,
    _validate_project_root,
    run_agent_task,
)
from ai_tech_lead.app_settings import ProjectRegistryEntry, parse_settings
from ai_tech_lead.backlog_refinement_capability import BacklogRefinementProposal
from ai_tech_lead.backlog_repository import BacklogRefinementDraft
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.progress_events import ProgressReporter, StdoutJsonlProgressSink
from ai_tech_lead.request_context import resolve_request_context
from ai_tech_lead.runtime_lock import RuntimeLockBusyError
from ai_tech_lead.target_project_context import (
    BacklogColumnContext,
    BacklogItemContext,
    BacklogProjectContext,
    TargetProjectContext,
)

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
    returns after calling _map_state_to_output), not raw graph state. The stubbed "final"
    target_project_context is just whatever the caller passed in — good enough for tests
    that aren't exercising in-graph backlog resolution.
    """
    calls: list[dict] = []

    def fake_execute(**kwargs: Any) -> tuple[dict[str, Any], Any]:
        calls.append(kwargs)
        return output, kwargs.get("target_project_context")

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


def _registry_entry(root: str, **overrides: Any) -> Any:
    entry = ProjectRegistryEntry(
        root=str(Path(root).resolve()),
        name=Path(root).name or root,
        platform="filesystem",
        required_credentials_env=[],
    )
    return replace(entry, **overrides)


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


def _execution_failed_output(summary: str = "Coding agent failed after retries.") -> dict[str, Any]:
    """Minimal output dict for a real execution attempt that ultimately failed."""
    return {
        "request_id": "",
        "status": STATUS_FAILED,
        "summary": summary,
        "formulated_task": "",
        "brief": "",
        "coding_agent_instruction": "implement it",
        "backend_used": "codex",
        "execution_performed": True,
        "logs": [],
        "evidence": [],
        "next_action": "Needs direct human review.",
        "result_kind": "terminal_failure",
        "pending_decision": None,
    }


def _make_fake_workflow_with_final_context(
    monkeypatch: pytest.MonkeyPatch,
    output: dict[str, Any],
    final_target_project_context: TargetProjectContext | None,
) -> list[dict]:
    """Like _make_fake_workflow, but the "final" context differs from the input.

    Simulates a backlog item the graph resolved mid-run (the known-backlog-
    resolution path in coding_workflow_graph.py) rather than one supplied up
    front — the scenario completion sync must now also handle.
    """
    calls: list[dict] = []

    def fake_execute(**kwargs: Any) -> tuple[dict[str, Any], TargetProjectContext | None]:
        calls.append(kwargs)
        return output, final_target_project_context

    monkeypatch.setattr("ai_tech_lead.agent_task_runner._execute_workflow", fake_execute)
    return calls


def _known_backlog_target_project_context(row_hash: str) -> TargetProjectContext:
    """A target_project_context as it would look right after the graph's
    known-backlog-resolution path fetched ATL-001 mid-run."""
    return TargetProjectContext(
        project_key="ai-tech-lead",
        backlog_project=BacklogProjectContext(
            project_key="ai-tech-lead",
            spreadsheet_id="spreadsheet-a",
            sheet_name=SHEET_NAME,
        ),
        backlog_item=BacklogItemContext(
            project_key="ai-tech-lead",
            spreadsheet_id="spreadsheet-a",
            sheet_name=SHEET_NAME,
            item_id="ATL-001",
            title="Existing item",
            row_hash=row_hash,
            fetched_at="2026-08-10T00:00:00+00:00",
        ),
    )


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
# project_root authorization
# ---------------------------------------------------------------------------


class TestValidateProjectRoot:
    def _settings(self, project_registry: list[ProjectRegistryEntry]) -> Any:
        settings = parse_settings(valid_settings_dict())
        return replace(settings, project_registry=project_registry)

    def test_none_input_returns_none(self) -> None:
        settings = self._settings([_registry_entry(str(Path("/allowed/path")))])
        assert _validate_project_root(None, settings) is None

    def test_registered_path_is_accepted(self, tmp_path: Path) -> None:
        root = str(tmp_path)
        settings = self._settings([_registry_entry(root)])
        assert _validate_project_root(root, settings) == str(Path(root).resolve())

    def test_unregistered_path_requires_explicit_human_approval(self, tmp_path: Path) -> None:
        settings = self._settings([_registry_entry("/some/other/path")])
        with pytest.raises(ValueError, match="has not been explicitly approved"):
            _validate_project_root(str(tmp_path), settings)

    def test_explicitly_approved_unregistered_path_is_accepted(self, tmp_path: Path) -> None:
        settings = self._settings([_registry_entry("/some/other/path")])
        result = _validate_project_root(str(tmp_path), settings, human_approved=True)
        assert result == str(tmp_path.resolve())

    def test_path_is_resolved_before_comparison(self, tmp_path: Path) -> None:
        root = str(tmp_path)
        settings = self._settings([_registry_entry(root)])
        result = _validate_project_root(root + "/", settings)
        assert result == str(Path(root).resolve())

    def test_registered_non_filesystem_platform_fails_closed(self, tmp_path: Path) -> None:
        settings = self._settings([_registry_entry(str(tmp_path), platform="remote-git")])
        with pytest.raises(ValueError, match="registered for platform 'remote-git'"):
            _validate_project_root(str(tmp_path), settings)

    def test_registered_root_with_missing_credentials_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ATL_TEST_TOKEN", raising=False)
        settings = self._settings(
            [_registry_entry(str(tmp_path), required_credentials_env=["ATL_TEST_TOKEN"])]
        )
        with pytest.raises(ValueError, match="required credentials are missing: ATL_TEST_TOKEN"):
            _validate_project_root(str(tmp_path), settings)

    def test_registered_root_with_missing_location_fails_closed(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing-project"
        settings = self._settings([_registry_entry(str(missing))])
        with pytest.raises(ValueError, match="location is unavailable"):
            _validate_project_root(str(missing), settings)


def test_project_root_not_registered_returns_failed_without_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(
        monkeypatch,
        {
            "project_registry": [_registry_entry("/some/other/path", name="Other Project")]
        },
    )

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "project_root": "/some/evil/path"},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "project_registry" in result["summary"]
    assert calls == [], "workflow must not run when project_root is rejected"


def test_project_root_in_registry_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed_root = str(tmp_path / "allowed-project")
    Path(allowed_root).mkdir(parents=True)
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(
        monkeypatch,
        {
            "project_registry": [_registry_entry(allowed_root, name="Allowed Project")]
        },
    )

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "project_root": allowed_root},
    )
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["target_project_context"].project_root == str(Path(allowed_root).resolve())


def test_unregistered_project_root_with_explicit_human_approval_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved_root = tmp_path / "approved-project"
    approved_root.mkdir()
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(
        monkeypatch,
        {
            "project_registry": [_registry_entry(PROJECT_ROOT, name="AI Tech Lead")]
        },
    )

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "project_root": str(approved_root), "human_approved": True},
    )
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["target_project_context"].project_root == str(approved_root.resolve())


def test_no_project_root_passes_none_to_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(tmp_path, {"task": "fix bug"})
    run_agent_task(input_file, output_file)

    assert len(calls) == 1
    assert calls[0]["target_project_context"].project_root == ""


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


def test_map_decision_context_clarification_is_plain_text() -> None:
    decision = _parse_decision(
        {"option": "answer", "text": "AF-052 is the agent-factory repo."}
    )
    payload = _map_decision_to_resume_payload("context_clarification", decision)
    assert payload == "AF-052 is the agent-factory repo."


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

    def __init__(self, pending_value: dict[str, Any], checkpointer: Any | None = None) -> None:
        self._pending_value = pending_value
        self._checkpointer = checkpointer
        self.invoke_calls: list[Any] = []
        self.configs: list[dict[str, Any]] = []
        self.resumed = False

    def get_state(self, _config: dict[str, Any]) -> _FakeSnapshot:
        if self.resumed:
            return _FakeSnapshot(None, paused=False)
        return _FakeSnapshot(self._pending_value, paused=True)

    def invoke(self, value: Any, *, config: dict[str, Any]) -> dict[str, Any]:
        self.invoke_calls.append(value)
        self.configs.append(config)
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
        if self._checkpointer is not None:
            self._checkpointer.checkpoint.metadata = dict(config.get("metadata", {}))
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


class _FakeCheckpoint:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}


class _FakeCheckpointer:
    def __init__(self) -> None:
        self.checkpoint = _FakeCheckpoint()

    def get_tuple(self, _config: dict[str, Any]) -> _FakeCheckpoint:
        return self.checkpoint


@pytest.mark.parametrize(
    ("task_kind", "execution_mode"),
    [("technical_analysis", "instruction_only"), ("coding_task", "execute")],
    ids=["technical-analysis", "execute-coding-task"],
)
def test_decision_only_resume_reuses_initial_request_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_kind: str,
    execution_mode: str,
) -> None:
    checkpointer = _FakeCheckpointer()
    graph = _FakeResumableGraph(
        {"kind": "approval", "reason": "risky", "formulated_task": ""}, checkpointer
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_graph", lambda **_kwargs: graph
    )
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.get_checkpointer", lambda: checkpointer)
    _stub_settings(monkeypatch, {"execute_coding_agent": True})

    request_id = f"req-{task_kind}"
    initial_input, initial_output = _write_input(
        tmp_path,
        {
            "request_id": request_id,
            "task": "Review the implementation.",
            "task_kind": task_kind,
            "execution_mode": execution_mode,
        },
    )
    assert run_agent_task(initial_input, initial_output) == 0
    expected_metadata = {"task_kind": task_kind, "execution_mode": execution_mode}
    assert checkpointer.checkpoint.metadata == expected_metadata

    resume_input, resume_output = _write_input(
        tmp_path,
        {"request_id": request_id, "decision": {"option": "cancel"}},
    )
    run_agent_task(resume_input, resume_output)

    assert graph.configs[0]["metadata"] == expected_metadata
    assert graph.configs[1]["metadata"] == expected_metadata


def test_execute_workflow_reuses_subprocess_thread_for_initial_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = "req-stable-thread"
    graph = _FakeResumableGraph(
        {"kind": "approval", "reason": "risky", "formulated_task": "Do the thing"}
    )
    monkeypatch.setattr(
        "ai_tech_lead.coding_workflow_graph.build_graph", lambda **_kwargs: graph
    )

    _execute_workflow(
        request_id=request_id,
        task="Do the thing",
        execute_coding_agent=False,
        task_kind="coding_task",
        execution_mode="instruction_only",
        target_project_context=None,
    )
    _execute_workflow(
        request_id=request_id,
        task="",
        execute_coding_agent=False,
        task_kind="coding_task",
        execution_mode="instruction_only",
        target_project_context=None,
        decision=_parse_decision({"option": "approve"}),
    )

    assert [config["configurable"]["thread_id"] for config in graph.configs] == [
        f"subprocess-{request_id}",
        f"subprocess-{request_id}",
    ]


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
        task_kind="coding_task",
        execution_mode="instruction_only",
        target_project_context=None,
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
            task_kind="coding_task",
            execution_mode="instruction_only",
            target_project_context=None,
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


@pytest.mark.parametrize(
    ("state", "state_snapshot", "execute_coding_agent"),
    [
        (
            {
                "agent_instruction": "",
                "coding_agent_success": False,
                "coding_agent_performed_by": "",
                "coding_agent_result": "",
            },
            _FakeSnapshot({"kind": "approval", "reason": "Needs a decision."}),
            False,
        ),
        (
            {
                "agent_instruction": "implement it",
                "coding_agent_success": None,
                "coding_agent_performed_by": "",
                "coding_agent_result": "",
            },
            None,
            False,
        ),
        (
            {
                "agent_instruction": "run this",
                "coding_agent_success": False,
                "coding_agent_performed_by": "",
                "coding_agent_result": "Git preflight blocked the launch.",
            },
            None,
            True,
        ),
    ],
    ids=["waiting-for-decision", "instruction-only", "terminal-failure"],
)
def test_map_state_to_output_reports_no_execution_without_backend_run(
    state: dict[str, Any], state_snapshot: Any | None, execute_coding_agent: bool
) -> None:
    result = _map_state_to_output(
        "req-no-execution",
        state,
        execute_coding_agent,
        state_snapshot=state_snapshot,
        thread_id="subprocess-req-no-execution",
    )

    assert result["execution_performed"] is False


def test_map_state_to_output_reports_execution_when_backend_is_recorded() -> None:
    result = _map_state_to_output(
        "req-execution",
        {
            "agent_instruction": "implement it",
            "coding_agent_success": True,
            "coding_agent_performed_by": "codex",
            "coding_agent_result": "Implemented.",
        },
        True,
    )

    assert result["execution_performed"] is True


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


@pytest.mark.parametrize(
    ("state", "state_snapshot", "execute_coding_agent"),
    [
        (
            {
                "agent_instruction": "",
                "coding_agent_success": False,
                "coding_agent_performed_by": "",
                "coding_agent_result": "",
            },
            _FakeSnapshot({"kind": "approval", "reason": "Needs a decision."}),
            False,
        ),
        (
            {
                "agent_instruction": "implement it",
                "coding_agent_success": None,
                "coding_agent_performed_by": "",
                "coding_agent_result": "",
            },
            None,
            False,
        ),
        (
            {
                "agent_instruction": "run this",
                "coding_agent_success": False,
                "coding_agent_performed_by": "",
                "coding_agent_result": "Git preflight blocked the launch.",
            },
            None,
            True,
        ),
    ],
    ids=["waiting-for-decision", "instruction-only", "terminal-failure"],
)
def test_map_state_to_output_reports_no_execution_without_backend_run(
    state: dict[str, Any], state_snapshot: Any | None, execute_coding_agent: bool
) -> None:
    result = _map_state_to_output(
        "req-no-execution",
        state,
        execute_coding_agent,
        state_snapshot=state_snapshot,
        thread_id="subprocess-req-no-execution",
    )

    assert result["execution_performed"] is False


def test_map_state_to_output_reports_execution_when_backend_is_recorded() -> None:
    result = _map_state_to_output(
        "req-execution",
        {
            "agent_instruction": "implement it",
            "coding_agent_success": True,
            "coding_agent_performed_by": "codex",
            "coding_agent_result": "Implemented.",
        },
        True,
    )

    assert result["execution_performed"] is True


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


def test_map_state_to_output_maps_project_guidance_governance_interrupt() -> None:
    result = _map_state_to_output(
        "req-guidance-pending",
        {
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
        },
        False,
        state_snapshot=_FakeSnapshot(
            {
                "kind": "project_guidance_governance",
                "status": "missing",
                "summary": "No rule covers database migrations.",
                "related_locations": [],
                "proposed_change": "Add a bullet under AGENTS.md's Universal rules about migrations.",
                "reason": "The task adds a schema migration.",
            }
        ),
        thread_id="subprocess-req-guidance-pending",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert result["pending_decision"]["kind"] == "project_guidance_governance"
    option_names = {opt["name"] for opt in result["pending_decision"]["options"]}
    assert option_names == {"approve", "reject"}
    assert "database migrations" in result["pending_decision"]["prompt"]
    assert "Add a bullet under AGENTS.md" in result["pending_decision"]["prompt"]


def test_map_state_to_output_rejected_project_guidance_proposal_fails_clearly() -> None:
    result = _map_state_to_output(
        "req-guidance-rejected",
        {
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
            "project_guidance_rejected_summary": (
                "Project guidance conflicting and the proposed change was not approved: "
                "AGENTS.md says use pytest; docs/INDEX.md says use unittest."
            ),
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-guidance-rejected",
    )

    assert result["status"] == STATUS_FAILED
    assert result["result_kind"] == "terminal_failure"
    assert "conflicting" in result["summary"]
    assert "pytest" in result["summary"]


def test_map_state_to_output_maps_context_clarification_interrupt() -> None:
    result = _map_state_to_output(
        "req-context-clarify",
        {
            "agent_instruction": "",
            "formulated_task": "",
            "brief": "",
            "orchestrator_input_required": True,
            "orchestrator_input_question": "What does AF-052 refer to, and where should I retrieve it from?",
            "coding_agent_success": False,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
        },
        False,
        state_snapshot=_FakeSnapshot(
            {
                "kind": "context_clarification",
                "question": "What does AF-052 refer to, and where should I retrieve it from?",
                "reason": "A referenced project resource could not be resolved safely.",
                "retry_count": 0,
            }
        ),
        thread_id="subprocess-req-context-clarify",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert result["pending_decision"]["kind"] == "context_clarification"
    option_names = {opt["name"] for opt in result["pending_decision"]["options"]}
    assert option_names == {"answer"}
    assert "AF-052" in result["pending_decision"]["prompt"]


def test_map_state_to_output_context_clarification_exhausted_fails_clearly() -> None:
    result = _map_state_to_output(
        "req-context-exhausted",
        {
            "agent_instruction": "",
            "formulated_task": "",
            "brief": "",
            "orchestrator_input_required": True,
            "orchestrator_input_question": "What does AF-052 refer to, and where should I retrieve it from?",
            "coding_agent_success": False,
            "coding_agent_result": "",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
            "context_clarification_exhausted": True,
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-context-exhausted",
    )

    assert result["status"] == STATUS_FAILED
    assert result["result_kind"] == "terminal_failure"
    assert result["pending_decision"] is None
    assert "AF-052" in result["summary"]
    assert "human review" in result["next_action"].lower()


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


def test_map_state_to_output_maps_integration_approval() -> None:
    result = _map_state_to_output(
        "req-integration",
        {
            "agent_instruction": "implement it",
            "formulated_task": "Add a logout button",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": True,
            "coding_agent_result": "Validated on the task branch.",
            "restart_required": False,
            "task_feedback": [],
            "research_source_titles": [],
            "verification_status": "complete",
            "git_lifecycle_enabled": True,
            "git_task_branch": "atl/task-abc123",
            "git_task_commit_sha": "task-sha",
            "git_main_status": "MAIN STATUS: NOT IN MAIN — pushed branch atl/task-abc123",
            "git_integration_status": "awaiting_approval",
        },
        True,
        state_snapshot=_FakeSnapshot(
            {
                "kind": "integration_approval",
                "task_branch": "atl/task-abc123",
                "task_commit_sha": "task-sha",
                "main_status": "MAIN STATUS: NOT IN MAIN — pushed branch atl/task-abc123",
                "prompt": "Validated work is pushed, but it is NOT in main.",
            }
        ),
        thread_id="subprocess-req-integration",
    )

    assert result["status"] == STATUS_WAITING_DECISION
    assert result["pending_decision"]["kind"] == "integration_approval"
    assert {opt["name"] for opt in result["pending_decision"]["options"]} == {
        "approve",
        "cancel",
    }
    assert result["main_status"].startswith("MAIN STATUS: NOT IN MAIN")


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


def test_map_state_to_output_reports_success_when_verified_run_requires_restart() -> None:
    result = _map_state_to_output(
        "req-verify-complete-restart",
        {
            "agent_instruction": "implement it",
            "formulated_task": "Add a logout button",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": True,
            "coding_agent_result": "Implemented.",
            "restart_required": True,
            "task_feedback": [],
            "research_source_titles": [],
            "verification_status": "complete",
            "verification_reason": "Acceptance criteria satisfied.",
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-verify-complete-restart",
    )

    assert result["status"] == STATUS_SUCCESS
    assert result["result_kind"] == "execution_result"


def test_map_state_to_output_distinguishes_pushed_branch_from_main() -> None:
    result = _map_state_to_output(
        "req-branch",
        {
            "agent_instruction": "Implement the task.",
            "coding_agent_success": True,
            "coding_agent_result": "validated",
            "verification_status": "complete",
            "git_lifecycle_enabled": True,
            "git_task_branch": "atl/task-abc123",
            "git_task_base_sha": "base-sha",
            "git_task_commit_sha": "task-sha",
            "git_task_worktree": "/tmp/worktree",
            "git_task_branch_pushed": True,
            "git_main_status": "MAIN STATUS: NOT IN MAIN — pushed branch atl/task-abc123",
            "git_integration_status": "not_requested",
        },
        True,
    )

    assert result["status"] == STATUS_SUCCESS
    assert "not in main" in result["summary"].lower()
    assert result["main_status"].startswith("MAIN STATUS: NOT IN MAIN")
    assert result["git"]["task_commit_sha"] == "task-sha"
    assert "completed" not in result["summary"].lower()


def test_map_state_to_output_reports_verified_main_sha() -> None:
    result = _map_state_to_output(
        "req-main",
        {
            "agent_instruction": "Implement the task.",
            "coding_agent_success": True,
            "coding_agent_result": "validated",
            "verification_status": "complete",
            "git_lifecycle_enabled": True,
            "git_task_branch": "atl/task-abc123",
            "git_task_commit_sha": "task-sha",
            "git_task_branch_pushed": True,
            "git_main_status": "MAIN STATUS: IN MAIN — verified on origin/main at main-sha",
            "git_main_sha": "main-sha",
            "git_integration_status": "integrated",
        },
        True,
    )

    assert result["status"] == STATUS_SUCCESS
    assert result["main_status"] == "MAIN STATUS: IN MAIN — verified on origin/main at main-sha"
    assert result["git"]["main_sha"] == "main-sha"


def test_map_state_to_output_reports_failed_when_verification_fails_and_restart_is_required() -> None:
    result = _map_state_to_output(
        "req-verify-failed-restart",
        {
            "agent_instruction": "implement it",
            "formulated_task": "Add a logout button",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": True,
            "coding_agent_result": "Implemented.",
            "restart_required": True,
            "task_feedback": [],
            "research_source_titles": [],
            "verification_status": "failed",
            "verification_reason": "The logout button does not sign the user out.",
        },
        False,
        state_snapshot=None,
        thread_id="subprocess-req-verify-failed-restart",
    )

    assert result["status"] == STATUS_FAILED
    assert result["result_kind"] == "terminal_failure"
    assert "does not sign the user out" in result["summary"]


def test_map_state_to_output_reports_failed_when_coding_fails_and_restart_is_required() -> None:
    result = _map_state_to_output(
        "req-coding-failed-restart",
        {
            "agent_instruction": "run this",
            "formulated_task": "Fix the task",
            "brief": "",
            "orchestrator_input_required": False,
            "orchestrator_input_question": "",
            "coding_agent_success": False,
            "coding_agent_result": "command exited 1",
            "restart_required": True,
            "task_feedback": [],
            "research_source_titles": [],
        },
        True,
    )

    assert result["status"] == STATUS_FAILED
    assert result["result_kind"] == "terminal_failure"
    assert "Coding agent failed" in result["summary"]


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
        workflow_callback = kwargs["workflow_progress_callback"]
        assert callable(workflow_callback)
        workflow_callback("resolving_context", "Resolving project scope and context.")
        workflow_callback("risk_review", "Reviewing execution risk and approval requirements.")
        return _Graph(kwargs["coding_agent_progress_callback"])

    monkeypatch.setattr("ai_tech_lead.coding_workflow_graph.build_graph", fake_build_graph)

    result, _final_target_project_context = _execute_workflow(
        request_id="req-graph-progress",
        task="Implement the change",
        execute_coding_agent=False,
        task_kind="coding_task",
        execution_mode="instruction_only",
        target_project_context=None,
        progress_reporter=reporter,
    )

    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["phase"] for event in events] == [
        "analysing",
        "resolving_context",
        "risk_review",
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


def test_repeated_backlog_read_failure_stops_before_work_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _execution_success_output())
    _stub_settings(monkeypatch)

    class _AlwaysUnavailableWorksheet:
        def __init__(self):
            self.read_calls = 0

        def get_all_values(self):
            self.read_calls += 1
            raise RequestsConnectionError("simulated transient Sheets outage")

    worksheet = _AlwaysUnavailableWorksheet()
    client = FakeClient({"spreadsheet-a": FakeSpreadsheet({SHEET_NAME: worksheet})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    monkeypatch.setattr("ai_tech_lead.backlog_sheets_repository.time.sleep", lambda _seconds: None)
    input_file, output_file = _write_input(
        tmp_path,
        {
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-001",
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 1
    assert worksheet.read_calls == 2
    assert calls == []
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


# ---------------------------------------------------------------------------
# backlog_reference + project_reference.backlog supplied together: must agree
# on the same resource, and share one column layout — never silently pick one.
# ---------------------------------------------------------------------------


def test_matching_backlog_reference_and_project_backlog_use_shared_custom_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same resource named both ways: the custom column layout from
    project_reference.backlog is used consistently for the initial fetch and
    for completion sync — not the default layout for one and custom for the
    other."""
    _stub_settings(monkeypatch)
    custom_header = ["Work ID", "Work Title", "Work Status", "Work Evidence"]
    worksheet = FakeWorksheet([custom_header, ["ATL-001", "Existing item", "Backlog", ""]])
    client = FakeClient({"spreadsheet-a": FakeSpreadsheet({SHEET_NAME: worksheet})})
    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: client})
    _make_fake_workflow(monkeypatch, _execution_success_output("Implemented the change."))

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-matching-layout-1",
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-001",
            },
            "project_reference": {
                "backlog": {
                    "project_key": "ai-tech-lead",
                    "spreadsheet_id": "spreadsheet-a",
                    "sheet_name": SHEET_NAME,
                    "columns": {
                        "item_id": "Work ID",
                        "title": "Work Title",
                        "status": "Work Status",
                        "evidence_validation": "Work Evidence",
                    },
                },
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["status"] == STATUS_SUCCESS
    # If the initial fetch had used the default "ID"/"Status" column names instead of
    # the custom ones, it would never have found the row at all (no "ID" column exists
    # in this sheet) and the request would have failed before execution ever ran.
    assert result["backlog_sync_status"] == "synced"

    written_row = worksheet.get_all_values()[1]
    assert written_row[2] == "Done"  # "Work Status"
    assert "req-matching-layout-1" in written_row[3]  # "Work Evidence"


def test_conflicting_backlog_reference_and_project_backlog_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two different backlog resources named for the same request must not be
    silently reconciled by picking one — and no Sheets work may happen before
    the conflict is caught."""
    _stub_settings(monkeypatch)
    calls = _make_fake_workflow(monkeypatch, _execution_success_output())

    class _ExplodingClient:
        def open_by_key(self, spreadsheet_id: str) -> Any:
            raise AssertionError("must not touch Sheets when backlog resources conflict")

    monkeypatch.setattr(sheets_mod, "_client_cache", {CREDENTIALS_PATH: _ExplodingClient()})

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-conflict-1",
            "task": "do the thing",
            "backlog_reference": {
                "spreadsheet_id": "spreadsheet-a",
                "sheet_name": SHEET_NAME,
                "item_id": "ATL-001",
            },
            "project_reference": {
                "backlog": {
                    "project_key": "ai-tech-lead",
                    "spreadsheet_id": "spreadsheet-b",
                    "sheet_name": SHEET_NAME,
                },
            },
        },
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 1
    result = _read_output(output_file)
    assert result["status"] == STATUS_FAILED
    assert "different backlog resources" in result["summary"]
    assert "spreadsheet-a" in result["summary"]
    assert "spreadsheet-b" in result["summary"]
    assert calls == []  # the workflow itself must never have run


# ---------------------------------------------------------------------------
# Completion sync for a backlog item resolved mid-graph (known-backlog path),
# not just one supplied up front via the top-level backlog_reference field.
# ---------------------------------------------------------------------------


def test_known_backlog_item_syncs_on_real_execution_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backlog item the graph resolved mid-run still writes back on success."""
    from ai_tech_lead.backlog_reference import BacklogReference
    from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository, compute_row_hash

    _stub_settings(monkeypatch)
    row = _row("ATL-001", "Existing item", status="Backlog")
    _install_fake_sheets_client(monkeypatch, "spreadsheet-a", row)
    context = _known_backlog_target_project_context(compute_row_hash(row))
    _make_fake_workflow_with_final_context(
        monkeypatch, _execution_success_output("Implemented the change."), context
    )
    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-known-backlog-1", "task": "Code ATL-001 next"},
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["status"] == STATUS_SUCCESS
    assert result["backlog_sync_status"] == "synced"

    repository = SheetsBacklogRepository(
        BacklogReference("ai-tech-lead", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    assert repository.get_item("ATL-001").status.value == "Done"


def test_known_backlog_item_not_synced_for_instruction_only_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An instruction_package (no real execution) must not touch the Sheet."""
    from ai_tech_lead.backlog_reference import BacklogReference
    from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository, compute_row_hash

    _stub_settings(monkeypatch)
    row = _row("ATL-001", "Existing item", status="Backlog")
    _install_fake_sheets_client(monkeypatch, "spreadsheet-a", row)
    context = _known_backlog_target_project_context(compute_row_hash(row))
    _make_fake_workflow_with_final_context(monkeypatch, _success_output(), context)
    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-known-backlog-2", "task": "Code ATL-001 next"},
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["backlog_sync_status"] == "not_applicable"

    repository = SheetsBacklogRepository(
        BacklogReference("ai-tech-lead", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    assert repository.get_item("ATL-001").status.value == "Backlog"


def test_known_backlog_item_not_synced_for_failed_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real execution attempt that ultimately failed must not mark Done."""
    from ai_tech_lead.backlog_reference import BacklogReference
    from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository, compute_row_hash

    _stub_settings(monkeypatch)
    row = _row("ATL-001", "Existing item", status="Backlog")
    _install_fake_sheets_client(monkeypatch, "spreadsheet-a", row)
    context = _known_backlog_target_project_context(compute_row_hash(row))
    _make_fake_workflow_with_final_context(monkeypatch, _execution_failed_output(), context)
    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-known-backlog-3", "task": "Code ATL-001 next"},
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 1
    result = _read_output(output_file)
    assert result["status"] == STATUS_FAILED
    assert result["backlog_sync_status"] == "not_applicable"

    repository = SheetsBacklogRepository(
        BacklogReference("ai-tech-lead", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    assert repository.get_item("ATL-001").status.value == "Backlog"


def test_known_backlog_item_conflict_when_row_changed_since_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row changed after the mid-graph fetch — nothing gets overwritten."""
    from ai_tech_lead.backlog_reference import BacklogReference
    from ai_tech_lead.backlog_sheets_repository import SheetsBacklogRepository, compute_row_hash

    _stub_settings(monkeypatch)
    stale_row = _row("ATL-001", "Existing item", status="Backlog")
    stale_row_hash = compute_row_hash(stale_row)
    # By completion time, someone edited the row in the Sheet directly.
    current_row = _row("ATL-001", "Existing item", status="In Progress")
    _install_fake_sheets_client(monkeypatch, "spreadsheet-a", current_row)
    context = _known_backlog_target_project_context(stale_row_hash)
    _make_fake_workflow_with_final_context(
        monkeypatch, _execution_success_output("Implemented the change."), context
    )
    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-known-backlog-4", "task": "Code ATL-001 next"},
    )

    exit_code = run_agent_task(input_file, output_file)

    assert exit_code == 0
    result = _read_output(output_file)
    assert result["backlog_sync_status"] == "conflict"

    repository = SheetsBacklogRepository(
        BacklogReference("ai-tech-lead", "spreadsheet-a", SHEET_NAME, ""),
        credentials_path=CREDENTIALS_PATH,
    )
    assert repository.get_item("ATL-001").status.value == "In Progress"


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
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={"references": ["AF-052", "docs/runbook.md"]},
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    assert [item.item_id for item in context.resource_references] == [
        "AF-052",
        "docs/runbook.md",
    ]


def test_legacy_resource_references_still_work_alongside_hub_references() -> None:
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={
            "resource_references": [{"item_id": "AF-052", "title": "legacy title"}],
            "references": ["AF-052", "AH-010"],
        },
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    # The legacy dict for AF-052 is kept as-is (not overwritten by Hub's bare string);
    # AH-010 is added from Hub's references since no legacy entry supplied it.
    assert [(item.item_id, item.title) for item in context.resource_references] == [
        ("AF-052", "legacy title"),
        ("AH-010", ""),
    ]


def test_hub_style_references_reach_the_existing_context_resolver() -> None:
    """A Hub `references` payload must resolve through resolve_request_context exactly
    like a legacy `resource_references` payload would — the AI Tech Lead boundary only
    normalizes shape; request_context.py remains the sole place resolution happens."""
    settings = parse_settings(valid_settings_dict())
    target_project_context = _build_target_project_context(
        task_input={"references": ["AF-052"]},
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    result = resolve_request_context(
        "Code AF-052 for Agent Factory", target_project_context=target_project_context
    )

    assert result["unresolved_references"] == []
    assert result["resolved_resource_references"] == ["AF-052"]
    assert result["clarification_question"] == ""


def test_hub_reference_not_matching_request_text_stays_unresolved() -> None:
    """A Hub reference that doesn't correspond to anything detected in the request text
    must not mask an unrelated unresolved reference — clarification still fires for it."""
    settings = parse_settings(valid_settings_dict())
    target_project_context = _build_target_project_context(
        task_input={"references": ["docs/runbook.md"]},
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    result = resolve_request_context(
        "Code AF-052 for Agent Factory", target_project_context=target_project_context
    )

    assert result["unresolved_references"] == ["AF-052"]
    assert result["clarification_question"] == (
        "What does AF-052 refer to, and where should I retrieve it from?"
    )


def test_project_reference_project_root_is_accepted_when_allowlisted() -> None:
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={
            "project_reference": {
                "project_key": "agent-factory",
                "project_root": settings.project_root,
            }
        },
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    assert context.project_key == "agent-factory"
    assert context.project_root == settings.project_root


def test_mismatched_top_level_and_project_reference_roots_are_rejected() -> None:
    settings = parse_settings(valid_settings_dict())

    with pytest.raises(ValueError, match="does not match project_reference.project_root"):
        _build_target_project_context(
            task_input={
                "project_root": settings.project_root,
                "project_reference": {
                    "project_root": "/different/project/root",
                },
            },
            settings=settings,
            backlog_reference=None,
            source_record=None,
            backlog_project=None,
        )


def test_project_context_valid_envelope_is_accepted() -> None:
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={
            "project_context": {
                "schema_version": 1,
                "project_root": settings.project_root,
                "references": ["AF-052"],
            }
        },
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    assert context.project_root == settings.project_root
    assert [item.item_id for item in context.resource_references] == ["AF-052"]


def test_project_context_unsupported_schema_version_is_rejected() -> None:
    settings = parse_settings(valid_settings_dict())

    with pytest.raises(ValueError, match="schema_version=99"):
        _build_target_project_context(
            task_input={"project_context": {"schema_version": 99, "project_root": settings.project_root}},
            settings=settings,
            backlog_reference=None,
            source_record=None,
            backlog_project=None,
        )


def test_project_context_missing_project_root_leaves_context_empty() -> None:
    """No project_root anywhere (neither project_context nor legacy top-level) resolves
    to an empty root rather than failing — only code paths that actually need a target
    project (require_project_root) fail clearly, at the point of use."""
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={"project_context": {"schema_version": 1, "references": []}},
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    assert context.project_root == ""
    with pytest.raises(ValueError, match="Target project root is required"):
        context.require_project_root("a test")


def test_project_context_references_map_into_resource_references() -> None:
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={"project_context": {"schema_version": 1, "references": ["AF-052", "AH-010"]}},
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    assert [item.item_id for item in context.resource_references] == ["AF-052", "AH-010"]


def test_project_context_conflicting_top_level_project_root_is_rejected() -> None:
    settings = parse_settings(valid_settings_dict())

    with pytest.raises(ValueError, match="project_context.project_root does not match"):
        _build_target_project_context(
            task_input={
                "project_root": settings.project_root,
                "project_context": {"schema_version": 1, "project_root": "/different/project/root"},
            },
            settings=settings,
            backlog_reference=None,
            source_record=None,
            backlog_project=None,
        )


def test_legacy_flat_project_root_and_references_still_work_without_project_context() -> None:
    """Bounded migration: callers that never send the new project_context envelope keep
    working exactly as before, through the same resolver rather than a second path."""
    settings = parse_settings(valid_settings_dict())
    context = _build_target_project_context(
        task_input={"project_root": settings.project_root, "references": ["AF-052"]},
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=None,
    )

    assert context.project_root == settings.project_root
    assert [item.item_id for item in context.resource_references] == ["AF-052"]


def test_backlog_project_context_stays_separate_from_project_context_envelope() -> None:
    """BacklogProjectContext (spreadsheet_id/sheet_name/columns) is an AI Tech Lead-owned
    extension carried through project_reference.backlog — it must not be affected by, or
    leak into, the universal project_context envelope."""
    settings = parse_settings(valid_settings_dict())
    task_input = {
        "project_context": {
            "schema_version": 1,
            "project_root": settings.project_root,
            "references": ["AF-052"],
        },
        "project_reference": {
            "backlog": {
                "spreadsheet_id": "sheet-123",
                "sheet_name": "Backlog",
                "item_id_prefix": "AF",
            }
        },
    }
    context = _build_target_project_context(
        task_input=task_input,
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=_resolve_backlog_project(task_input),
    )

    assert context.project_root == settings.project_root
    assert context.backlog_project is not None
    assert context.backlog_project.spreadsheet_id == "sheet-123"
    assert context.backlog_project.sheet_name == "Backlog"
    assert [item.item_id for item in context.resource_references] == ["AF-052"]


def test_hub_backlog_refinement_returns_waiting_decision_with_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)

    class _FakeStore:
        saved: dict[str, Any] = {}

        def save_pending(self, *, request_id: str, proposal: Any) -> None:
            self.saved[request_id] = proposal

        def get_pending(self, request_id: str) -> Any | None:
            return self.saved.get(request_id)

        def clear_pending(self, request_id: str) -> None:
            self.saved.pop(request_id, None)

    monkeypatch.setattr("ai_tech_lead.agent_task_runner.BacklogRefinementStore", _FakeStore)
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.repository_for", lambda *args, **kwargs: object())
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.infer_backlog_item_prefix", lambda _repo: "HUB")
    monkeypatch.setattr(
        "ai_tech_lead.agent_task_runner.prepare_backlog_refinement_proposal",
        lambda **_kwargs: BacklogRefinementProposal(
            draft=BacklogRefinementDraft(
                item_id="HUB-002",
                title="Add backlog-only mode",
                creator="Human",
                item_type="Story",
                epic="Backlog Management",
                priority="High",
                size="M",
                approval_required=True,
                approval_reason="Creates a new product capability.",
                problem="Hub needs a first-class backlog refinement entrypoint.",
                desired_outcome="Allow Hub to request backlog refinement safely.",
                scope=["Add Hub backlog refinement mode"],
                out_of_scope=["Change the coding workflow"],
                acceptance_criteria=["Hub can request a backlog refinement draft"],
                duplicate_check_result="No duplicate found.",
                stale_check_result="No stale item found.",
                already_done_check_result="Not already done.",
                research_required=False,
                research_cache_used=[],
                external_research_needed=False,
                recommended_implementation_pattern="Reuse the existing refinement service.",
                patterns_explicitly_rejected=["Second workflow"],
                freshness_risk="Low.",
                implementation_guidance="Keep the capability outside the coding graph.",
                approval_risk_flags=["Writes backlog items"],
            ),
            source="fake-model",
            skill_path=".skills/backlog-item-authoring/SKILL.md",
            matches=(),
            blocked=False,
        ),
    )

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "hub-refine-1",
            "task_kind": "backlog_refinement",
            "task": "Add backlog-only mode",
            "project_reference": {
                "backlog": {
                    "project_key": "agent-hub",
                    "spreadsheet_id": "spreadsheet-hub",
                    "sheet_name": "Hub Backlog",
                    "item_id_prefix": "HUB",
                }
            },
        },
    )

    rc = run_agent_task(input_file, output_file)
    result = _read_output(output_file)

    assert rc == 0
    assert result["status"] == STATUS_WAITING_DECISION
    assert result["result_kind"] == "backlog_refinement_draft"
    assert result["backlog_refinement"]["draft"]["item_id"] == "HUB-002"
    assert result["pending_decision"]["kind"] == "backlog_refinement_approval"


def test_hub_backlog_refinement_duplicate_blocking_returns_clarification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.repository_for", lambda *args, **kwargs: object())
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.infer_backlog_item_prefix", lambda _repo: "ATL")
    monkeypatch.setattr(
        "ai_tech_lead.agent_task_runner.prepare_backlog_refinement_proposal",
        lambda **_kwargs: BacklogRefinementProposal(
            draft=BacklogRefinementDraft(
                item_id="ATL-002",
                title="Add backlog support",
                creator="Human",
                item_type="Story",
                epic="Backlog Management",
                priority="High",
                size="M",
                approval_required=True,
                approval_reason="Writes backlog items.",
                problem="Need backlog support.",
                desired_outcome="Backlog support exists.",
                scope=["Backlog refinement"],
                out_of_scope=["Coding workflow changes"],
                acceptance_criteria=["Draft exists"],
                duplicate_check_result="Existing related item found.",
                stale_check_result="No stale item found.",
                already_done_check_result="Not already done.",
                research_required=False,
                research_cache_used=[],
                external_research_needed=False,
                recommended_implementation_pattern="Reuse existing service.",
                patterns_explicitly_rejected=["Second workflow"],
                freshness_risk="Low.",
                implementation_guidance="Do not create a duplicate.",
                approval_risk_flags=["Writes backlog items"],
            ),
            source="fake-model",
            skill_path=".skills/backlog-item-authoring/SKILL.md",
            matches=(
                type(
                    "_Match",
                    (),
                    {
                        "item_id": "ATL-001",
                        "title": "Existing backlog support",
                        "status": "Backlog",
                        "kind": "likely_duplicate",
                        "reason": "Very similar title to an existing backlog item.",
                        "to_payload": lambda self: {
                            "item_id": self.item_id,
                            "title": self.title,
                            "status": self.status,
                            "kind": self.kind,
                            "reason": self.reason,
                        },
                    },
                )(),
            ),
            blocked=True,
        ),
    )

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "hub-refine-dup",
            "task_kind": "backlog_refinement",
            "task": "Add backlog support",
            "project_reference": {
                "backlog": {
                    "project_key": "ai-tech-lead",
                    "spreadsheet_id": "spreadsheet-a",
                    "sheet_name": "Backlog",
                }
            },
        },
    )

    rc = run_agent_task(input_file, output_file)
    result = _read_output(output_file)

    assert rc == 0
    assert result["status"] == STATUS_NEEDS_CLARIFICATION
    assert "ATL-001" in result["summary"]
    assert result["pending_decision"] is None


def test_hub_backlog_refinement_approval_writes_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_settings(monkeypatch)
    created: list[str] = []

    class _FakeStore:
        saved: dict[str, Any] = {
            "hub-refine-approve": BacklogRefinementProposal(
                draft=BacklogRefinementDraft(
                    item_id="HUB-002",
                    title="Add backlog-only mode",
                    creator="Human",
                    item_type="Story",
                    epic="Backlog Management",
                    priority="High",
                    size="M",
                    approval_required=True,
                    approval_reason="Writes backlog items.",
                    problem="Need backlog-only mode.",
                    desired_outcome="Hub can request backlog refinement.",
                    scope=["Hub backlog refinement"],
                    out_of_scope=["Coding workflow changes"],
                    acceptance_criteria=["Item is written after approval"],
                    duplicate_check_result="No duplicate found.",
                    stale_check_result="No stale item found.",
                    already_done_check_result="Not already done.",
                    research_required=False,
                    research_cache_used=[],
                    external_research_needed=False,
                    recommended_implementation_pattern="Reuse existing service.",
                    patterns_explicitly_rejected=["Second workflow"],
                    freshness_risk="Low.",
                    implementation_guidance="Keep approval explicit.",
                    approval_risk_flags=["Writes backlog items"],
                ),
                source="fake-model",
                skill_path=".skills/backlog-item-authoring/SKILL.md",
                matches=(),
                blocked=False,
            )
        }

        def save_pending(self, *, request_id: str, proposal: Any) -> None:
            self.saved[request_id] = proposal

        def get_pending(self, request_id: str) -> Any | None:
            proposal = self.saved.get(request_id)
            if proposal is None:
                return None
            return type("_Pending", (), {"proposal": proposal})()

        def clear_pending(self, request_id: str) -> None:
            self.saved.pop(request_id, None)

    class _FakeRepo:
        def add_refined_item(self, draft: BacklogRefinementDraft) -> Any:
            created.append(draft.item_id)
            return type("_Item", (), {"item_id": draft.item_id, "title": draft.title})()

    monkeypatch.setattr("ai_tech_lead.agent_task_runner.BacklogRefinementStore", _FakeStore)
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.repository_for", lambda *args, **kwargs: _FakeRepo())
    monkeypatch.setattr("ai_tech_lead.agent_task_runner.infer_backlog_item_prefix", lambda _repo: "HUB")

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "hub-refine-approve",
            "task_kind": "backlog_refinement",
            "decision": {"option": "approve"},
            "project_reference": {
                "backlog": {
                    "project_key": "agent-hub",
                    "spreadsheet_id": "spreadsheet-hub",
                    "sheet_name": "Hub Backlog",
                    "item_id_prefix": "HUB",
                }
            },
        },
    )

    rc = run_agent_task(input_file, output_file)
    result = _read_output(output_file)

    assert rc == 0
    assert created == ["HUB-002"]
    assert result["status"] == STATUS_SUCCESS
    assert result["result_kind"] == "backlog_item_created"


def test_build_target_project_context_parses_backlog_project_configuration() -> None:
    settings = parse_settings(valid_settings_dict())
    task_input = {
        "project_reference": {
            "backlog": {
                "project_key": "agent-hub",
                "spreadsheet_id": "spreadsheet-hub",
                "sheet_name": "Hub Backlog",
                "item_id_prefix": "HUB",
                "columns": {
                    "item_id": "Work ID",
                    "title": "Work Title",
                },
            }
        }
    }

    context = _build_target_project_context(
        task_input=task_input,
        settings=settings,
        backlog_reference=None,
        source_record=None,
        backlog_project=_resolve_backlog_project(task_input),
    )

    assert context.backlog_project == BacklogProjectContext(
        project_key="agent-hub",
        spreadsheet_id="spreadsheet-hub",
        sheet_name="Hub Backlog",
        item_id_prefix="HUB",
        columns=BacklogColumnContext(item_id="Work ID", title="Work Title"),
    )
