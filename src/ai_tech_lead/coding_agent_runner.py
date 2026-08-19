"""Controlled subprocess runner for real coding-agent execution.

The coding agent is launched as a separate CLI process with its working directory set to
the project root. Any file changes it makes are written directly to disk; the
current Python process must be restarted before it can import changed Python
code from those files.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.logging_setup import AGENT_LOGGER_NAME, LOGGER_NAME
from ai_tech_lead.runtime_lock import acquire_project_execution_lock

logger = logging.getLogger(LOGGER_NAME)
agent_logger = logging.getLogger(AGENT_LOGGER_NAME)

_POLL_INTERVAL_SECONDS = 1.0
# Protocol-owned startup limits keep backend health diagnostics bounded; the
# configured coding-agent runtime timeout remains user-owned in AppSettings.
_BACKEND_PREFLIGHT_TIMEOUT_SECONDS = 5.0
_BACKEND_PREFLIGHT_OUTPUT_MAX_CHARS = 512


@dataclass
class _RunOutcome:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    cancelled: bool


class CodingAgentCancellationToken:
    """Thread-safe cancellation handle for one coding-agent subprocess."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None

    def attach_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._process = process
            if self._event.is_set() and process.poll() is None:
                process.terminate()

    def cancel(self) -> bool:
        self._event.set()
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False
            process.terminate()
            return True

    def is_cancel_requested(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class CodingAgentResult:
    """Captured result from a coding-agent subprocess run."""

    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    message: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_seconds: float = 0.0
    success: bool = False
    changed_files_before: tuple[str, ...] = ()
    changed_files_after: tuple[str, ...] = ()
    changed_files_delta: tuple[str, ...] = ()

    def summary(self) -> str:
        command_text = _display_command(self.command)
        parts: list[str] = [self.message or "Coding agent subprocess completed."]

        if self.duration_seconds > 0:
            parts.append(f"Duration: {self.duration_seconds:.1f}s")

        if self.command:
            if self.changed_files_delta:
                parts.append(
                    f"Changed files ({len(self.changed_files_delta)}): "
                    + ", ".join(self.changed_files_delta[:10])
                )
            else:
                parts.append("Changed files: none detected")
            parts.append(
                "Token usage: not available from the current configured coding-agent backend."
            )

        parts += [
            f"Command: {command_text}",
            f"Return code: {self.returncode}",
            f"Timed out: {self.timed_out}",
            "Stdout:",
            self.stdout.strip() or "<empty>",
            "Stderr:",
            self.stderr.strip() or "<empty>",
        ]
        return "\n".join(parts)


def _preflight_coding_agent(project_root: Path, settings: AppSettings) -> str:
    """Verify the configured backend reports a version before it can launch.

    The runner owns this backend health boundary so workflow code stays generic.
    The reported version is diagnostic only; no compatibility decision is made
    without an authoritative provider or project rule.
    """
    version_command = [settings.coding_agent_command, "--version"]
    try:
        completed = subprocess.run(
            version_command,
            cwd=project_root.resolve(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            timeout=_BACKEND_PREFLIGHT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Coding agent backend preflight failed: command not found: "
            f"{settings.coding_agent_command}"
        ) from error
    except PermissionError as error:
        raise RuntimeError(
            "Coding agent backend preflight failed: command is not executable: "
            f"{settings.coding_agent_command}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "Coding agent backend preflight timed out after "
            f"{_BACKEND_PREFLIGHT_TIMEOUT_SECONDS:g}s: "
            f"{settings.coding_agent_command} --version"
        ) from error
    except OSError as error:
        raise RuntimeError(
            "Coding agent backend preflight could not execute "
            f"{settings.coding_agent_command}: {error}"
        ) from error

    diagnostic = _bounded_preflight_output(completed.stdout, completed.stderr)
    if completed.returncode != 0:
        detail = f" Diagnostic: {diagnostic}" if diagnostic else ""
        raise RuntimeError(
            "Coding agent backend preflight failed: "
            f"{settings.coding_agent_command} --version exited with "
            f"{completed.returncode}.{detail}"
        )
    if not diagnostic:
        raise RuntimeError(
            "Coding agent backend preflight failed: "
            f"{settings.coding_agent_command} --version returned no usable output."
        )

    logger.info(
        "Coding agent backend preflight passed: command=%s version=%s",
        settings.coding_agent_command,
        diagnostic,
    )
    return diagnostic


def _bounded_preflight_output(stdout: str | None, stderr: str | None) -> str:
    """Normalize and bound version diagnostics without interpreting them."""
    output = " | ".join(value.strip() for value in (stdout, stderr) if value and value.strip())
    if len(output) <= _BACKEND_PREFLIGHT_OUTPUT_MAX_CHARS:
        return output
    return output[:_BACKEND_PREFLIGHT_OUTPUT_MAX_CHARS] + "..."


def run_coding_agent(
    agent_instruction: str,
    project_root: Path,
    settings: AppSettings,
    progress_callback: Callable[[str], None] | None = None,
    cancellation_token: CodingAgentCancellationToken | None = None,
    use_pty: bool = False,
    sandbox_override: str | None = None,
) -> CodingAgentResult:
    """Run the configured CLI agent.

    use_pty=True for long interactive runs (actual coding) so the agent sees a
    real terminal and emits live progress. Leave False (default) for plan
    requests where plain stdout text extraction is required.
    """
    if not settings.execute_coding_agent:
        return CodingAgentResult(
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            message="Coding agent execution disabled by settings.",
        )

    instruction = agent_instruction.strip()
    if not instruction:
        raise ValueError("Agent instruction cannot be empty.")

    _preflight_coding_agent(project_root, settings)
    command = [settings.coding_agent_command, *settings.coding_agent_args]
    if sandbox_override:
        command.extend(["-s", sandbox_override])
    command.append(instruction)
    timeout_seconds = settings.max_runtime_minutes * 60
    progress_interval = settings.coding_agent_progress_interval_seconds

    changed_files_before = _get_git_changed_files(project_root)
    started_at = time.time()
    project_root = project_root.resolve()
    lock_lease = acquire_project_execution_lock(project_root)
    logger.info("Project execution lock acquired for %s (pid=%s)", project_root, os.getpid())
    try:
        run_fn = _run_with_pty if use_pty else _run_with_pipes
        outcome = run_fn(
            command=command,
            project_root=project_root,
            timeout_seconds=timeout_seconds,
            progress_interval=progress_interval,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
            settings=settings,
        )
    finally:
        lock_lease.release()

    ended_at = time.time()
    changed_files_after = _get_git_changed_files(project_root)
    files_delta = _files_delta(changed_files_before, changed_files_after)

    base = dict(
        command=command,
        returncode=outcome.returncode,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=ended_at - started_at,
        changed_files_before=changed_files_before,
        changed_files_after=changed_files_after,
        changed_files_delta=files_delta,
    )

    if outcome.cancelled:
        return CodingAgentResult(
            **base,
            cancelled=True,
            message="The coding agent was cancelled by the operator.",
        )

    if outcome.timed_out:
        return CodingAgentResult(
            **base,
            timed_out=True,
            message=f"The coding agent timed out after {timeout_seconds} seconds.",
        )

    success = outcome.returncode == 0
    message = (
        f"The coding agent finished successfully. System exit code: {outcome.returncode}."
        if success
        else (
            f"The coding agent reported a failure. System exit code: {outcome.returncode}. "
            "It exited with a non-zero return code."
        )
    )
    return CodingAgentResult(**base, success=success, message=message)


def _run_with_pty(
    *,
    command: list[str],
    project_root: Path,
    timeout_seconds: float,
    progress_interval: int,
    progress_callback: Callable[[str], None] | None,
    cancellation_token: CodingAgentCancellationToken | None,
    settings: AppSettings,
) -> _RunOutcome:
    """Launch the agent with pipes and stdin closed.

    PTY was tried but codex enters TUI/fullscreen mode when stdout is a TTY,
    emitting no newlines and producing no readable output. Pipes with
    stdin=DEVNULL give codex an EOF on stdin so it proceeds, and plain text
    output flows line-by-line through the pipe.
    """
    try:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            bufsize=1,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Coding agent command not found: {settings.coding_agent_command}"
        ) from error

    if cancellation_token is not None:
        cancellation_token.attach_process(process)

    output_lines: list[str] = []
    started = time.time()
    last_update_at = started
    last_reported_count = 0
    timed_out = False
    cancelled = False

    def _collect() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            if line:
                output_lines.append(line)
                agent_logger.info(line)

    reader = threading.Thread(target=_collect, daemon=True)
    reader.start()

    try:
        while process.poll() is None:
            elapsed = time.time() - started

            if cancellation_token is not None and cancellation_token.is_cancel_requested():
                cancelled = True
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                break

            if elapsed >= timeout_seconds:
                timed_out = True
                process.kill()
                break

            time.sleep(_POLL_INTERVAL_SECONDS)

            if time.time() - last_update_at >= progress_interval:
                new_lines = output_lines[last_reported_count:]
                last_reported_count = len(output_lines)
                _emit_progress_update(
                    progress_callback,
                    new_lines=new_lines,
                    elapsed_seconds=elapsed,
                    total_lines=last_reported_count,
                )
                last_update_at = time.time()

    except (KeyboardInterrupt, SystemExit):
        process.kill()
        raise

    if not timed_out and not cancelled:
        process.wait()

    reader.join(timeout=5)

    return _RunOutcome(
        stdout="\n".join(output_lines),
        stderr="",
        returncode=process.returncode,
        timed_out=timed_out,
        cancelled=cancelled,
    )


def _run_with_pipes(
    *,
    command: list[str],
    project_root: Path,
    timeout_seconds: float,
    progress_interval: int,
    progress_callback: Callable[[str], None] | None,
    cancellation_token: CodingAgentCancellationToken | None,
    settings: AppSettings,
) -> _RunOutcome:
    """Launch the agent with stdout/stderr pipes — for plan requests expecting plain text."""
    try:
        process = subprocess.Popen(
            ["stdbuf", "-oL", *command],
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            bufsize=1,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Coding agent command not found: {settings.coding_agent_command}"
        ) from error

    if cancellation_token is not None:
        cancellation_token.attach_process(process)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _collect(stream: object, target: list[str]) -> None:
        for raw_line in stream:  # type: ignore[union-attr]
            line = raw_line.rstrip("\n")
            if line:
                target.append(line)
                agent_logger.info(line)

    stdout_thread = threading.Thread(
        target=_collect, args=(process.stdout, stdout_lines), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_collect, args=(process.stderr, stderr_lines), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    cancelled = False
    started = time.time()
    last_update_at = started
    last_reported_stdout = 0
    last_reported_stderr = 0

    try:
        while process.poll() is None:
            elapsed = time.time() - started

            if cancellation_token is not None and cancellation_token.is_cancel_requested():
                cancelled = True
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                break

            if elapsed >= timeout_seconds:
                timed_out = True
                process.kill()
                break

            if time.time() - last_update_at >= progress_interval:
                new_stdout = stdout_lines[last_reported_stdout:]
                new_stderr = stderr_lines[last_reported_stderr:]
                last_reported_stdout = len(stdout_lines)
                last_reported_stderr = len(stderr_lines)
                new_lines = new_stdout or [f"[stderr] {line}" for line in new_stderr]
                _emit_progress_update(
                    progress_callback,
                    new_lines=new_lines,
                    elapsed_seconds=elapsed,
                    total_lines=last_reported_stdout + last_reported_stderr,
                )
                last_update_at = time.time()

            time.sleep(_POLL_INTERVAL_SECONDS)

    except (KeyboardInterrupt, SystemExit):
        process.kill()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        raise

    stdout_thread.join(timeout=10)
    stderr_thread.join(timeout=10)

    if not timed_out and not cancelled:
        process.wait()

    return _RunOutcome(
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
        returncode=process.returncode,
        timed_out=timed_out,
        cancelled=cancelled,
    )


@dataclass(frozen=True)
class GitPreflightResult:
    """Read-only inspection of the target worktree, taken right before the
    coding-agent subprocess launches.

    `status` is one of "clean", "proceed_unrelated", "blocked_relevant",
    "blocked_ambiguous", or "blocked_inspection_failed". This inspection never
    resets, stashes, checks out, stages, or otherwise modifies pre-existing
    work — it only reads `git status`/`git rev-parse`.
    """

    status: str
    branch: str
    head: str
    dirty_paths: tuple[str, ...]
    reason: str

    @property
    def safe_to_proceed(self) -> bool:
        return self.status in ("clean", "proceed_unrelated")


@dataclass(frozen=True)
class _GitStatusEntry:
    path: str
    raw: str
    ambiguous: bool


# Rename (R), copy (C), and unmerged/conflict (U) status codes touch more than
# one path or an unresolved conflict, so a single-path overlap check cannot
# safely classify them as related or unrelated to the approved task.
_AMBIGUOUS_GIT_STATUS_CODES = frozenset({"R", "C", "U"})


def run_git_preflight(project_root: Path, relevance_text: str) -> GitPreflightResult:
    """Inspect the worktree immediately before the coding-agent subprocess launches.

    Fails closed: any error while reading git state blocks execution rather
    than treating the repository as clean. `relevance_text` is the already-
    approved plan/task text used to decide whether a pre-existing dirty path
    overlaps the work about to be launched — no additional LLM call is made.
    """
    try:
        branch = _git_rev_parse(project_root, "--abbrev-ref", "HEAD")
        head = _git_rev_parse(project_root, "HEAD")
        entries = _git_status_entries(project_root)
    except Exception as error:
        return GitPreflightResult(
            status="blocked_inspection_failed",
            branch="",
            head="",
            dirty_paths=(),
            reason=f"Git preflight inspection failed: {error}",
        )

    dirty_paths = tuple(entry.path for entry in entries)

    if not entries:
        return GitPreflightResult(
            status="clean", branch=branch, head=head, dirty_paths=(), reason="Worktree is clean."
        )

    ambiguous_entries = [entry for entry in entries if entry.ambiguous]
    if ambiguous_entries:
        return GitPreflightResult(
            status="blocked_ambiguous",
            branch=branch,
            head=head,
            dirty_paths=dirty_paths,
            reason=(
                "A pre-existing change cannot be safely classified as related or "
                f"unrelated to the approved task: {ambiguous_entries[0].raw.strip()!r}"
            ),
        )

    relevant_paths: list[str] = []
    uncertain_paths: list[str] = []
    for entry in entries:
        classification = _classify_path_relevance(entry.path, relevance_text)
        if classification == "relevant":
            relevant_paths.append(entry.path)
        elif classification == "uncertain":
            uncertain_paths.append(entry.path)

    if relevant_paths:
        return GitPreflightResult(
            status="blocked_relevant",
            branch=branch,
            head=head,
            dirty_paths=dirty_paths,
            reason=f"Pre-existing change overlaps the approved task: {', '.join(relevant_paths)}",
        )

    if uncertain_paths:
        return GitPreflightResult(
            status="blocked_ambiguous",
            branch=branch,
            head=head,
            dirty_paths=dirty_paths,
            reason=(
                "A pre-existing change has an uncertain relationship to the approved task "
                f"and cannot be safely classified as unrelated: {', '.join(uncertain_paths)}"
            ),
        )

    return GitPreflightResult(
        status="proceed_unrelated",
        branch=branch,
        head=head,
        dirty_paths=dirty_paths,
        reason="Pre-existing changes are unrelated to the approved task; left untouched.",
    )


def _git_rev_parse(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _git_status_entries(cwd: Path) -> tuple[_GitStatusEntry, ...]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status --porcelain=v1 failed: {result.stderr.strip()}")
    return tuple(
        _parse_git_status_line(line) for line in result.stdout.splitlines() if line.strip()
    )


def _parse_git_status_line(line: str) -> _GitStatusEntry:
    if len(line) < 4:
        raise ValueError(f"Unrecognized git status line: {line!r}")
    index_status, worktree_status, rest = line[0], line[1], line[3:]
    ambiguous = (
        index_status in _AMBIGUOUS_GIT_STATUS_CODES
        or worktree_status in _AMBIGUOUS_GIT_STATUS_CODES
    )
    if " -> " in rest:
        _, _, path = rest.partition(" -> ")
    else:
        path = rest
    path = path.strip()
    if not path:
        raise ValueError(f"Unrecognized git status line: {line!r}")
    return _GitStatusEntry(path=path, raw=line, ambiguous=ambiguous)


# Generic scaffolding directory/file-stem names that show up in almost any
# repository regardless of what the task is about (e.g. a plan that mentions
# "add tests" would otherwise flag every dirty file under tests/). Structural
# vocabulary, not project- or domain-specific, so excluding it from the
# partial-match tier does not hardcode any product rule.
_GENERIC_PATH_TOKENS = frozenset(
    {
        "src",
        "lib",
        "libs",
        "app",
        "apps",
        "bin",
        "dist",
        "build",
        "docs",
        "doc",
        "test",
        "tests",
        "data",
        "config",
        "configs",
        "scripts",
        "assets",
        "public",
        "static",
        "vendor",
    }
)


def _classify_path_relevance(path: str, relevance_text: str) -> str:
    """Classify one dirty path against the already-approved task text as
    "relevant", "uncertain", or "unrelated" — no LLM call, no repo scan.

    "relevant": the full path, its filename, or a nested (multi-segment)
    ancestor directory of it is explicitly named in the text — e.g. the plan,
    formulated task, code-recon report, or project-guidance locations already
    gathered earlier in this same approved workflow run.

    "uncertain": no explicit match, but the file's immediate directory name or
    filename stem (a single, generic-enough word) appears in the text — too
    weak a signal to safely call the file unrelated, so it blocks for human
    review rather than silently proceeding.

    "unrelated": no textual signal at all. This is the only positive evidence
    obtainable without scanning the whole repository or making another LLM
    call, so it is the bar for allowing a dirty file through untouched.
    """
    if not relevance_text:
        return "unrelated"
    haystack = relevance_text.casefold()

    if path.casefold() in haystack:
        return "relevant"

    candidate = Path(path)
    basename = candidate.name
    if basename and basename.casefold() in haystack:
        return "relevant"

    parent = candidate.parent
    if str(parent) not in ("", ".") and "/" in str(parent) and str(parent).casefold() in haystack:
        return "relevant"

    weak_tokens = [candidate.stem, parent.name]
    for token in weak_tokens:
        normalized = token.casefold()
        if len(normalized) < 4 or normalized in _GENERIC_PATH_TOKENS:
            continue
        if re.search(rf"\b{re.escape(normalized)}\b", haystack):
            return "uncertain"

    return "unrelated"


def _get_git_changed_files(cwd: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            return ()
        files: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) > 3:
                files.append(line[3:].strip())
        return tuple(files)
    except Exception:
        return ()


def _files_delta(before: tuple[str, ...], after: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(after) - set(before)))


def _display_command(command: list[str]) -> str:
    if not command:
        return "<not run>"
    if len(command) == 1:
        return command[0]
    return " ".join([*command[:-1], "<agent_instruction>"])


def _emit_progress_update(
    progress_callback: Callable[[str], None] | None,
    *,
    new_lines: list[str],
    elapsed_seconds: float,
    total_lines: int,
) -> None:
    elapsed_text = _format_elapsed_seconds(elapsed_seconds)
    if new_lines:
        agent_logger.debug("[%s] %d new lines", elapsed_text, len(new_lines))
        if progress_callback is not None:
            try:
                progress_callback(
                    f"[{elapsed_text}] Coding agent running — {total_lines} lines of output so far"
                )
            except Exception:
                logger.exception("Coding agent progress callback failed.")
    else:
        logger.debug("[%s] Coding agent still running, no new output.", elapsed_text)
        if progress_callback is not None:
            try:
                progress_callback(f"[{elapsed_text}] Still running... ({total_lines} lines so far)")
            except Exception:
                logger.exception("Coding agent progress callback failed.")


def _format_elapsed_seconds(elapsed_seconds: float) -> str:
    total_seconds = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes == 0:
        return f"{seconds}s"
    return f"{minutes}m {seconds}s"
