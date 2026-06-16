from __future__ import annotations

import subprocess
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_agent_runner import run_coding_agent


def _install_fake_pipe_popen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "done",
    stderr: str = "",
    poll_calls_before_done: int = 0,
) -> list[dict[str, object]]:
    """Fake for pipe-mode runs (use_pty=False, the default)."""
    calls: list[dict[str, object]] = []

    class FakePopen:
        def __init__(self, args: object, **kwargs: object) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            self.args = args
            self.returncode: int | None = None
            self._poll_calls = 0
            self._returncode = returncode
            self.stdout = StringIO((stdout + "\n") if stdout else "")
            self.stderr = StringIO((stderr + "\n") if stderr else "")

        def poll(self) -> int | None:
            if self._poll_calls < poll_calls_before_done:
                self._poll_calls += 1
                return None
            self.returncode = self._returncode
            return self._returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = self._returncode
            return self._returncode

        def kill(self) -> None:
            self.returncode = -9

        def terminate(self) -> None:
            self.returncode = self._returncode

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    return calls



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
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout="", stderr=""),
    )
    calls = _install_fake_pipe_popen(monkeypatch, stdout="done")

    result = run_coding_agent("Do the task", tmp_path, settings)

    agent_call = calls[0]
    # Pipe mode prepends stdbuf -oL for line-buffered output.
    assert agent_call["args"] == [
        "stdbuf", "-oL",
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "Do the task",
    ]
    assert agent_call["kwargs"]["cwd"] == tmp_path
    assert agent_call["kwargs"]["stdout"] is subprocess.PIPE
    assert agent_call["kwargs"]["stderr"] is subprocess.PIPE
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

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=2, stdout="", stderr=""),
    )
    _install_fake_pipe_popen(monkeypatch, returncode=2, stdout="", stderr="bad args")

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
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout="ok", stderr=""
        ),
    )
    _install_fake_pipe_popen(monkeypatch, returncode=0, stdout="ok")

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.success is True
    assert "finished successfully" in result.message
    assert "finished successfully" in result.summary()
    assert "return code 0" not in result.summary().lower()
    assert "Codex" not in result.summary()
    assert "codex" in result.summary()


def test_run_coding_agent_failure_uses_friendly_wording(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0], returncode=1, stdout="", stderr="err"
        ),
    )
    _install_fake_pipe_popen(monkeypatch, returncode=1, stdout="", stderr="err")

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
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout="", stderr=""),
    )
    _install_fake_pipe_popen(monkeypatch, returncode=0, stdout="")

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
            stdout = "" if git_call_count == 1 else " M src/new_file.py\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _install_fake_pipe_popen(monkeypatch, returncode=0, stdout="done")

    result = run_coding_agent("Do the task", tmp_path, settings)

    assert result.changed_files_delta == ("src/new_file.py",)
    assert "Changed files (1)" in result.summary()
    assert "src/new_file.py" in result.summary()


def test_run_coding_agent_emits_progress_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout="", stderr=""),
    )
    _install_fake_pipe_popen(
        monkeypatch, returncode=0, stdout="agent output line", poll_calls_before_done=2
    )

    progress_messages: list[str] = []

    result = run_coding_agent(
        "Do the task",
        tmp_path,
        settings,
        progress_callback=progress_messages.append,
        use_pty=True,
    )

    assert len(progress_messages) >= 1
    assert any("Coding agent running" in m for m in progress_messages)
    assert result.stdout == "agent output line"
