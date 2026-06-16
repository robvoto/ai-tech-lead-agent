"""Tests for agent_task_runner security model and I/O contract.

The workflow graph is always stubbed out — these tests verify the security
layer (input validation, project_root allowlist, execution gate, approval
tokens) without requiring LLM calls or a real LangGraph runtime.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.agent_task_runner import (
    STATUS_APPROVAL_REQUIRED,
    STATUS_FAILED,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_SUCCESS,
    _validate_project_root,
    run_agent_task,
)
from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.approval_store import consume_approval_token, create_approval_token


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
    }


def _approval_required_output(reason: str = "risky change") -> dict[str, Any]:
    """Minimal output dict that _execute_workflow returns when approval is needed."""
    return {
        "request_id": "",
        "status": STATUS_APPROVAL_REQUIRED,
        "summary": f"Approval required: {reason}",
        "formulated_task": "",
        "brief": "",
        "coding_agent_instruction": "",
        "backend_used": "none",
        "execution_performed": False,
        "logs": [],
        "evidence": [],
        "next_action": "Approve via Telegram, then resubmit with human_approved=true and the approval_token.",
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


# ---------------------------------------------------------------------------
# project_root allowlist
# ---------------------------------------------------------------------------


class TestValidateProjectRoot:
    def _settings(self, allowed_roots: list[str]) -> Any:
        settings = parse_settings(valid_settings_dict())
        return replace(settings, army_allowed_project_roots=allowed_roots)

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
    _stub_settings(monkeypatch, {"army_allowed_project_roots": ["/some/other/path"]})

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
    _stub_settings(monkeypatch, {"army_allowed_project_roots": [allowed_root]})

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


# ---------------------------------------------------------------------------
# Approval token
# ---------------------------------------------------------------------------


class TestApprovalStore:
    def test_issued_token_can_be_consumed(self) -> None:
        token = create_approval_token("req-1", "fix the bug")
        assert consume_approval_token(token, "req-1", "fix the bug") is True

    def test_token_is_single_use(self) -> None:
        token = create_approval_token("req-2", "fix the bug")
        consume_approval_token(token, "req-2", "fix the bug")
        assert consume_approval_token(token, "req-2", "fix the bug") is False

    def test_unknown_token_is_rejected(self) -> None:
        assert consume_approval_token("not-a-real-token", "req-unknown", "fix the bug") is False

    def test_empty_token_is_rejected(self) -> None:
        assert consume_approval_token("", "req-unknown", "fix the bug") is False

    def test_expired_token_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ai_tech_lead.approval_store as ap_mod

        token = create_approval_token("req-3", "fix the bug")

        # Advance time past TTL
        monkeypatch.setattr(ap_mod, "TOKEN_TTL_SECONDS", -1)
        future_time = int(time.time()) + 7200
        monkeypatch.setattr("ai_tech_lead.approval_store.time.time", lambda: float(future_time))

        assert consume_approval_token(token, "req-3", "fix the bug") is False

    def test_token_is_bound_to_request_and_task(self) -> None:
        token = create_approval_token("req-4", "fix the bug")

        assert consume_approval_token(token, "req-4", "fix the wrong bug") is False
        assert consume_approval_token(token, "req-5", "fix the bug") is False
        assert consume_approval_token(token, "req-4", "fix the bug") is True


def test_human_approved_without_token_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "human_approved": True},  # no approval_token
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "approval_token" in result["summary"].lower()
    assert calls == []


def test_human_approved_with_invalid_token_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {"task": "fix bug", "human_approved": True, "approval_token": "invalid-uuid"},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert calls == []


def test_human_approved_with_valid_token_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    token = create_approval_token("req-flow-1", "fix the bug")

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-flow-1",
            "task": "fix the bug",
            "human_approved": True,
            "approval_token": token,
        },
    )
    rc = run_agent_task(input_file, output_file)

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["human_approved"] is True


def test_human_approved_token_must_match_request_and_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    token = create_approval_token("req-bind-1", "fix the bug")

    input_file, output_file = _write_input(
        tmp_path,
        {
            "request_id": "req-bind-1",
            "task": "fix the wrong bug",
            "human_approved": True,
            "approval_token": token,
        },
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 1
    assert result["status"] == STATUS_FAILED
    assert "approval_token" in result["summary"].lower()
    assert calls == []


def test_approval_required_response_includes_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_fake_workflow(monkeypatch, _approval_required_output("risky migration"))
    _stub_settings(monkeypatch)

    input_file, output_file = _write_input(
        tmp_path,
        {"request_id": "req-approval-1", "task": "drop a table"},
    )
    rc = run_agent_task(input_file, output_file)

    result = _read_output(output_file)
    assert rc == 0
    assert result["status"] == STATUS_APPROVAL_REQUIRED
    assert "approval_token" in result, (
        "approval_token must be present in approval_required responses"
    )
    token = result["approval_token"]
    assert len(token) == 36, "token must be a UUID"
    # Token should be consumable exactly once
    assert consume_approval_token(token, "req-approval-1", "drop a table") is True
    assert consume_approval_token(token, "req-approval-1", "drop a table") is False


def test_approval_token_cannot_be_reused_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call with the same token must be rejected even if output said approval_required."""
    _make_fake_workflow(monkeypatch, _success_output())
    _stub_settings(monkeypatch)

    # Issue a real token
    token = create_approval_token("req-reuse", "fix the bug")

    # First approved call — consumes the token
    i1, o1 = _write_input(
        tmp_path / "first",
        {
            "request_id": "req-reuse",
            "task": "fix bug",
            "human_approved": True,
            "approval_token": token,
        },
    )
    run_agent_task(i1, o1)

    # Second approved call with the same token — must be rejected
    i2, o2 = _write_input(
        tmp_path / "second",
        {
            "request_id": "req-reuse",
            "task": "fix bug",
            "human_approved": True,
            "approval_token": token,
        },
    )
    rc = run_agent_task(i2, o2)

    result = _read_output(o2)
    assert rc == 1
    assert result["status"] == STATUS_FAILED


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
    }
    assert required_keys.issubset(result.keys())
    assert result["status"] == STATUS_SUCCESS
