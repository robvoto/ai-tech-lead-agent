from __future__ import annotations

import subprocess
from pathlib import Path

from ai_tech_lead.completion_evidence import capture_completion_evidence


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    (root / "billing").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "billing" / "invoices.py").write_text("y = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    return root, base_sha


def test_captures_diff_paths_and_passing_validation(tmp_path: Path) -> None:
    root, base_sha = _repo(tmp_path)
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")

    evidence = capture_completion_evidence(
        checkout_root=root,
        base_sha=base_sha,
        validation_command='python3 -c "import sys; sys.exit(0)"',
        relevance_text="update app.py behaviour",
        timeout_seconds=30,
    )

    assert "app.py" in evidence.diff_summary
    assert evidence.changed_files == ("app.py",)
    assert evidence.validation_passed is True
    assert evidence.out_of_scope_paths == ()


def test_failing_validation_reports_false_with_tail(tmp_path: Path) -> None:
    root, base_sha = _repo(tmp_path)
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")

    evidence = capture_completion_evidence(
        checkout_root=root,
        base_sha=base_sha,
        validation_command='python3 -c "print(\'boom\'); import sys; sys.exit(1)"',
        relevance_text="update app.py",
        timeout_seconds=30,
    )

    assert evidence.validation_passed is False
    assert "boom" in evidence.validation_output_tail


def test_missing_command_is_none_not_false(tmp_path: Path) -> None:
    root, base_sha = _repo(tmp_path)
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")

    evidence = capture_completion_evidence(
        checkout_root=root,
        base_sha=base_sha,
        validation_command="",
        relevance_text="update app.py",
        timeout_seconds=30,
    )

    assert evidence.validation_passed is None
    assert "No canonical validation command" in evidence.validation_output_tail


def test_unrunnable_command_is_none(tmp_path: Path) -> None:
    root, base_sha = _repo(tmp_path)
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")

    evidence = capture_completion_evidence(
        checkout_root=root,
        base_sha=base_sha,
        validation_command="definitely-not-a-real-command-xyz",
        relevance_text="update app.py",
        timeout_seconds=30,
    )

    assert evidence.validation_passed is None
    assert "not found" in evidence.validation_output_tail


def test_out_of_scope_paths_come_from_the_git_diff_not_a_report(tmp_path: Path) -> None:
    root, base_sha = _repo(tmp_path)
    # The task is about app.py; the agent also touched billing/invoices.py and
    # would not have to report it — git shows it regardless.
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")
    (root / "billing" / "invoices.py").write_text("y = 2\n", encoding="utf-8")

    evidence = capture_completion_evidence(
        checkout_root=root,
        base_sha=base_sha,
        validation_command="",
        relevance_text="update the app.py rendering behaviour",
        timeout_seconds=30,
    )

    assert set(evidence.changed_files) == {"app.py", "billing/invoices.py"}
    assert evidence.out_of_scope_paths == ("billing/invoices.py",)
