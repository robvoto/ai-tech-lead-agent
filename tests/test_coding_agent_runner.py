from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_agent_runner import run_coding_agent

from helpers import valid_settings_dict


def test_run_coding_agent_returns_disabled_result_without_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    settings = parse_settings(valid_settings_dict())

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.returncode is None
    assert result.command == []
    assert "disabled by settings" in result.summary()


def test_run_coding_agent_calls_configured_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_coding_agent("Do the task", tmp_path, settings)

    # calls[0] = git status --short (before), calls[1] = coding agent, calls[2] = git status (after)
    agent_call = next(c for c in calls if c["args"][0][0] != "git")
    assert agent_call["args"][0] == [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "Do the task",
    ]
    assert agent_call["kwargs"]["cwd"] == tmp_path
    assert agent_call["kwargs"]["timeout"] == 1200
    assert agent_call["kwargs"]["capture_output"] is True
    assert agent_call["kwargs"]["text"] is True
    assert agent_call["kwargs"]["shell"] is False
    assert result.returncode == 0
    assert result.stdout == "done"


def test_run_coding_agent_rejects_empty_instruction_when_enabled(tmp_path: Path) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)

    with pytest.raises(ValueError, match="Agent instruction cannot be empty"):
        run_coding_agent("   ", tmp_path, settings)


def test_run_coding_agent_reports_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout="", stderr="bad args")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.returncode == 2
    assert "non-zero" in result.summary()
    assert "bad args" in result.summary()


def test_run_coding_agent_success_uses_friendly_wording(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout="ok", stderr=""),
    )

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.success is True
    assert "finished successfully" in result.message
    assert "finished successfully" in result.summary()
    assert "return code 0" not in result.summary().lower()


def test_run_coding_agent_failure_uses_friendly_wording(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=1, stdout="", stderr="err"),
    )

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.success is False
    assert "reported a failure" in result.message
    assert "reported a failure" in result.summary()


def test_run_coding_agent_records_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout="", stderr=""),
    )

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.started_at > 0
    assert result.ended_at >= result.started_at
    assert result.duration_seconds >= 0
    assert "Duration:" in result.summary()


def test_run_coding_agent_records_changed_files_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    git_call_count = 0

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal git_call_count
        cmd = args[0]
        if isinstance(cmd, list) and cmd[0] == "git":
            git_call_count += 1
            # First git call (before): no changes; second (after): one new file
            stdout = "" if git_call_count == 1 else " M src/new_file.py\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.changed_files_delta == ("src/new_file.py",)
    assert "Changed files (1)" in result.summary()
    assert "src/new_file.py" in result.summary()
