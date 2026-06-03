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
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="done",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert calls
    assert calls[0]["args"][0] == [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "Do the task",
    ]
    assert calls[0]["kwargs"]["cwd"] == tmp_path
    assert calls[0]["kwargs"]["timeout"] == 1200
    assert calls[0]["kwargs"]["capture_output"] is True
    assert calls[0]["kwargs"]["text"] is True
    assert calls[0]["kwargs"]["shell"] is False
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
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=2,
            stdout="",
            stderr="bad args",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.returncode == 2
    assert "non-zero" in result.summary()
    assert "bad args" in result.summary()
