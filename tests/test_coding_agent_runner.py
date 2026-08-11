from __future__ import annotations

import subprocess
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.coding_agent_runner import run_coding_agent, run_git_preflight
from ai_tech_lead.runtime_lock import RuntimeLockBusyError


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


def test_run_coding_agent_raises_when_project_execution_lock_is_busy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), execute_coding_agent=True)
    monkeypatch.setattr(
        "ai_tech_lead.coding_agent_runner.acquire_project_execution_lock",
        lambda _project_root: (_ for _ in ()).throw(
            RuntimeLockBusyError("Runtime lock busy for project-execution: /tmp/project")
        ),
    )

    with pytest.raises(RuntimeLockBusyError, match="project-execution"):
        run_coding_agent("Do the task", tmp_path, settings)


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


def _fake_git_run(
    *,
    status_stdout: str = "",
    status_returncode: int = 0,
    branch: str = "main",
    head: str = "abc123def456",
) -> tuple[list[list[str]], object]:
    """Fake `subprocess.run` for `run_git_preflight`'s read-only git calls."""
    calls: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        calls.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            if cmd[2:] == ["--abbrev-ref", "HEAD"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=f"{branch}\n", stderr=""
                )
            if cmd[2:] == ["HEAD"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=f"{head}\n", stderr=""
                )
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=status_returncode, stdout=status_stdout, stderr=""
            )
        raise AssertionError(f"Unexpected subprocess call: {cmd}")

    return calls, fake_run


def test_run_git_preflight_clean_repo_reports_clean_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, fake_run = _fake_git_run(status_stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Do the task")

    assert result.status == "clean"
    assert result.safe_to_proceed is True
    assert result.dirty_paths == ()
    assert result.branch == "main"
    assert result.head == "abc123def456"
    # Preflight is read-only: never resets, stashes, or checks out.
    assert all(call[1] in ("status", "rev-parse") for call in calls)


def test_run_git_preflight_unrelated_dirty_file_allows_proceed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls, fake_run = _fake_git_run(status_stdout=" M unrelated_file.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Implement the new billing export feature")

    assert result.status == "proceed_unrelated"
    assert result.safe_to_proceed is True
    assert result.dirty_paths == ("unrelated_file.py",)
    # The pre-existing file is left untouched: only read-only git calls were made.
    assert all(call[1] in ("status", "rev-parse") for call in calls)


def test_run_git_preflight_relevant_dirty_file_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_git_run_calls, fake_run = _fake_git_run(status_stdout=" M src/billing_export.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(
        tmp_path, "Plan:\nUpdate src/billing_export.py to add CSV support."
    )

    assert result.status == "blocked_relevant"
    assert result.safe_to_proceed is False
    assert "billing_export.py" in result.reason


def test_run_git_preflight_ambiguous_rename_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _calls, fake_run = _fake_git_run(status_stdout="R  old_name.py -> new_name.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Do the task")

    assert result.status == "blocked_ambiguous"
    assert result.safe_to_proceed is False


def test_run_git_preflight_unmerged_conflict_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _calls, fake_run = _fake_git_run(status_stdout="UU conflict.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Do the task")

    assert result.status == "blocked_ambiguous"
    assert result.safe_to_proceed is False


def test_run_git_preflight_inspection_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Do the task")

    assert result.status == "blocked_inspection_failed"
    assert result.safe_to_proceed is False
    assert result.dirty_paths == ()


def test_run_git_preflight_nonzero_status_returncode_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _calls, fake_run = _fake_git_run(status_stdout="", status_returncode=128)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Do the task")

    assert result.status == "blocked_inspection_failed"
    assert result.safe_to_proceed is False


def test_run_git_preflight_captures_branch_head_and_multiple_dirty_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _calls, fake_run = _fake_git_run(
        status_stdout=" M unrelated_one.py\n?? unrelated_two.py\n",
        branch="feature/atl-079",
        head="cafef00d",
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Do the task")

    assert result.branch == "feature/atl-079"
    assert result.head == "cafef00d"
    assert result.dirty_paths == ("unrelated_one.py", "unrelated_two.py")
    assert result.status == "proceed_unrelated"


def test_run_git_preflight_directory_and_stem_token_match_blocks_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Filename-literal matching alone would call this "unrelated" — the plan
    never spells out "service.py" — but the words "auth" and "service" are
    both present, a real signal that should not be silently waved through.
    """
    _calls, fake_run = _fake_git_run(status_stdout=" M src/auth/service.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(
        tmp_path, "Update the auth service to reject expired tokens."
    )

    assert result.status == "blocked_ambiguous"
    assert result.safe_to_proceed is False
    assert "src/auth/service.py" in result.reason


def test_run_git_preflight_nested_ancestor_directory_mention_blocks_relevant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plan that scopes work to a nested directory (without naming the
    exact file) is explicit enough evidence to block outright, not just flag
    as uncertain.
    """
    _calls, fake_run = _fake_git_run(status_stdout=" M src/billing/export.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(
        tmp_path, "Plan:\nAll changes are scoped to src/billing/."
    )

    assert result.status == "blocked_relevant"
    assert result.safe_to_proceed is False


def test_run_git_preflight_generic_directory_token_does_not_cause_false_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coincidentally sharing a generic directory name like "tests" with the
    plan text should not block every dirty file under tests/ — that would
    make the gate unusable on any repo doing normal test-writing work.
    """
    _calls, fake_run = _fake_git_run(status_stdout=" M tests/test_something.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Add tests for the new tests runner.")

    assert result.status == "proceed_unrelated"
    assert result.safe_to_proceed is True


def test_run_git_preflight_no_textual_signal_still_proceeds_unrelated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Documents the known residual gap: with no code-recon report, no
    directory/stem mention, and no exact path mention anywhere in the
    already-approved task text, there is no deterministic signal left to
    classify on without a whole-repo scan or another LLM call — both out of
    scope for this slice — so this case proceeds untouched rather than
    blocking every run.
    """
    _calls, fake_run = _fake_git_run(status_stdout=" M src/auth/service.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_git_preflight(tmp_path, "Fix the login validation bug.")

    assert result.status == "proceed_unrelated"
