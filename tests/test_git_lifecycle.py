from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ai_tech_lead.git_lifecycle as lifecycle
from ai_tech_lead.git_lifecycle import (
    GitLifecycleBlocked,
    commit_and_push_task_branch,
    integrate_task_branch,
    parse_integration_approval,
    prepare_task_worktree,
    validate_integrated_git_result,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _clone_with_main(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    canonical = tmp_path / "canonical"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(tmp_path, "init", str(seed))
    _run(seed, "config", "user.email", "test@example.com")
    _run(seed, "config", "user.name", "Test User")
    (seed / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    _run(seed, "add", "service.py")
    _run(seed, "commit", "-m", "initial")
    _run(seed, "branch", "-M", "main")
    _run(seed, "remote", "add", "origin", str(remote))
    _run(seed, "push", "-u", "origin", "main")
    _run(tmp_path, "clone", "-b", "main", str(remote), str(canonical))
    _run(canonical, "config", "user.email", "test@example.com")
    _run(canonical, "config", "user.name", "Test User")
    return remote, seed, canonical


def test_integration_approval_parser_is_exact() -> None:
    assert parse_integration_approval("approved")
    assert parse_integration_approval("merge it")
    assert parse_integration_approval("put it in main")
    assert not parse_integration_approval("approved, and deploy it")
    assert not parse_integration_approval("looks good")


def test_task_branch_is_pushed_then_integrated_and_verified(tmp_path, monkeypatch) -> None:
    _remote, _seed, canonical = _clone_with_main(tmp_path)
    monkeypatch.setattr(lifecycle, "_WORKTREE_ROOT", tmp_path / "worktrees")

    task = prepare_task_worktree(canonical, "request-1")
    (Path(task.worktree) / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
    pushed = commit_and_push_task_branch(task)

    assert pushed.branch_pushed is True
    assert pushed.task_commit_sha
    assert pushed.main_status.startswith("MAIN STATUS: NOT IN MAIN")
    assert _git(canonical, "rev-parse", "origin/main") == pushed.base_sha

    integrated = integrate_task_branch(
        pushed,
        validate_integrated=validate_integrated_git_result,
    )

    assert integrated.main_status.startswith("MAIN STATUS: IN MAIN")
    assert _git(canonical, "rev-parse", "origin/main") == integrated.main_sha
    assert _git(canonical, "show", "origin/main:service.py") == "VALUE = 2"
    _run(canonical, "merge-base", "--is-ancestor", pushed.task_commit_sha, "origin/main")


def test_main_advancement_is_reconciled_without_force_push(tmp_path, monkeypatch) -> None:
    _remote, seed, canonical = _clone_with_main(tmp_path)
    monkeypatch.setattr(lifecycle, "_WORKTREE_ROOT", tmp_path / "worktrees")
    task = prepare_task_worktree(canonical, "request-2")
    (Path(task.worktree) / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
    pushed = commit_and_push_task_branch(task)

    (seed / "README.md").write_text("main advanced\n", encoding="utf-8")
    _run(seed, "add", "README.md")
    _run(seed, "commit", "-m", "advance main")
    _run(seed, "push", "origin", "main")

    integrated = integrate_task_branch(
        pushed,
        validate_integrated=validate_integrated_git_result,
    )

    assert integrated.main_status.startswith("MAIN STATUS: IN MAIN")
    assert _git(canonical, "show", "origin/main:README.md") == "main advanced"
    assert _git(canonical, "show", "origin/main:service.py") == "VALUE = 2"


def test_real_reconciliation_conflict_stops_and_preserves_task_branch(
    tmp_path, monkeypatch
) -> None:
    _remote, seed, canonical = _clone_with_main(tmp_path)
    monkeypatch.setattr(lifecycle, "_WORKTREE_ROOT", tmp_path / "worktrees")
    task = prepare_task_worktree(canonical, "request-3")
    (Path(task.worktree) / "service.py").write_text("VALUE = 2\n", encoding="utf-8")
    pushed = commit_and_push_task_branch(task)

    (seed / "service.py").write_text("VALUE = 99\n", encoding="utf-8")
    _run(seed, "add", "service.py")
    _run(seed, "commit", "-m", "conflicting main change")
    _run(seed, "push", "origin", "main")

    with pytest.raises(GitLifecycleBlocked, match="cannot be reconciled"):
        integrate_task_branch(pushed, validate_integrated=validate_integrated_git_result)

    assert _git(Path(pushed.worktree), "status", "--porcelain") == ""
    assert _git(canonical, "show", "origin/main:service.py") == "VALUE = 99"
