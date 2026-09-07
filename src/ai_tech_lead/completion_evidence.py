"""Independent post-run completion evidence (ATL-039).

After the coding agent reports success, the AI Tech Lead gathers its *own*
evidence before deciding completion, rather than trusting the agent's prose:

- a git diff summary taken directly from the checkout the agent worked in;
- the result of running the project's canonical validation command itself
  (``shell=False``), so the pass/fail signal is ATL's own return code;
- an advisory list of changed files that look unrelated to the approved task.

``validation_passed`` is tri-state: ``True``/``False`` when ATL actually ran the
command, ``None`` when there was no command to run or it could not be executed —
``None`` routes completion to human verification, it never counts as a pass.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

_DIFF_TIMEOUT_SECONDS = 20
_MAX_DIFF_SUMMARY_CHARS = 2000
_MAX_OUTPUT_TAIL_CHARS = 2000
_MAX_OUTPUT_TAIL_LINES = 40
_MAX_OUT_OF_SCOPE_PATHS = 20
_PATH_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class CompletionEvidence:
    diff_summary: str
    validation_command: str
    validation_passed: bool | None
    validation_output_tail: str
    out_of_scope_paths: tuple[str, ...]


def capture_completion_evidence(
    *,
    checkout_root: Path,
    base_sha: str,
    changed_files: tuple[str, ...],
    validation_command: str,
    relevance_text: str,
    timeout_seconds: int,
) -> CompletionEvidence:
    """Gather diff, validation and out-of-scope evidence for one completed run."""

    diff_summary = _diff_summary(checkout_root, base_sha)
    passed, output_tail = _run_validation(
        checkout_root, validation_command, timeout_seconds
    )
    out_of_scope = _out_of_scope_paths(changed_files, relevance_text)
    return CompletionEvidence(
        diff_summary=diff_summary,
        validation_command=validation_command,
        validation_passed=passed,
        validation_output_tail=output_tail,
        out_of_scope_paths=out_of_scope,
    )


def _diff_summary(checkout_root: Path, base_sha: str) -> str:
    """A readable name-status + shortstat view of what actually changed.

    Compared against the task base commit when known, otherwise the current
    ``HEAD``; either way this covers staged and unstaged working-tree changes,
    which is where a coding agent's edits sit before the branch is finalised.
    """

    ref = base_sha.strip() or "HEAD"
    name_status = _git_text(checkout_root, ["diff", ref, "--name-status"])
    shortstat = _git_text(checkout_root, ["diff", ref, "--shortstat"])
    if name_status is None and shortstat is None:
        return "(git diff unavailable in the coding checkout)"
    parts = []
    if name_status:
        parts.append(name_status)
    if shortstat:
        parts.append(shortstat)
    summary = "\n".join(parts).strip() or "(no differences reported by git)"
    if len(summary) > _MAX_DIFF_SUMMARY_CHARS:
        summary = summary[: _MAX_DIFF_SUMMARY_CHARS - 1].rstrip() + "…"
    return summary


def _git_text(checkout_root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=checkout_root,
            capture_output=True,
            text=True,
            timeout=_DIFF_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _run_validation(
    checkout_root: Path, validation_command: str, timeout_seconds: int
) -> tuple[bool | None, str]:
    """Run the canonical validation command and return (passed, output tail).

    ``passed`` is ``None`` when ATL had no command or could not execute one — the
    caller treats that as "cannot prove completion", never as success.
    """

    command = validation_command.strip()
    if not command:
        return None, "No canonical validation command was determined for this project."
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None, f"Validation command could not be parsed: {command!r}"
    if not tokens:
        return None, "Validation command was empty after parsing."

    try:
        completed = subprocess.run(
            tokens,
            cwd=checkout_root,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_seconds),
            shell=False,
            check=False,
        )
    except FileNotFoundError:
        return None, f"Validation command not found: {tokens[0]!r}"
    except subprocess.TimeoutExpired:
        return None, f"Validation command timed out after {timeout_seconds}s: {command}"
    except OSError as error:
        return None, f"Validation command could not be executed: {error}"

    tail = _bounded_tail(f"{completed.stdout}\n{completed.stderr}")
    return completed.returncode == 0, tail


def _bounded_tail(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-_MAX_OUTPUT_TAIL_LINES:])
    if len(tail) > _MAX_OUTPUT_TAIL_CHARS:
        tail = "…" + tail[-(_MAX_OUTPUT_TAIL_CHARS - 1) :]
    return tail or "(validation command produced no output)"


def _out_of_scope_paths(
    changed_files: tuple[str, ...], relevance_text: str
) -> tuple[str, ...]:
    """Advisory: changed files whose path shares no token with the approved task.

    Surfaced to the verifier and the logs, never a hard block — the acceptance
    criterion is that material out-of-scope changes are *detected and surfaced*.
    """

    haystack = relevance_text.lower()
    flagged: list[str] = []
    for path in changed_files:
        tokens = {t for t in _PATH_TOKEN_RE.findall(path.lower()) if len(t) >= 4}
        if tokens and not any(token in haystack for token in tokens):
            flagged.append(path)
        if len(flagged) >= _MAX_OUT_OF_SCOPE_PATHS:
            break
    return tuple(flagged)
