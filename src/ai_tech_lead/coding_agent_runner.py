"""Controlled subprocess runner for real coding-agent execution.

The coding agent is launched as a separate CLI process with its working directory set to
the project root. Any file changes it makes are written directly to disk; the
current Python process must be restarted before it can import changed Python
code from those files.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
import subprocess
import threading
import time
import logging

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# How often the poll loop wakes up to check process state and fire progress updates.
_POLL_INTERVAL_SECONDS = 1.0

# Maximum lines of recent stdout included in each progress update sent to the operator.
_PROGRESS_PREVIEW_LINES = 5


class CodingAgentCancellationToken:
    """Thread-safe cancellation handle for one coding-agent subprocess."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def attach_process(self, process: subprocess.Popen[str]) -> None:
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
        """Return a log-friendly summary of the subprocess outcome."""

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


def run_coding_agent(
    agent_instruction: str,
    project_root: Path,
    settings: AppSettings,
    progress_callback: Callable[[str], None] | None = None,
    cancellation_token: CodingAgentCancellationToken | None = None,
) -> CodingAgentResult:
    """Run the configured CLI agent for real when settings explicitly allow execution."""

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

    command = [
        settings.coding_agent_command,
        *settings.coding_agent_args,
        instruction,
    ]
    timeout_seconds = settings.max_runtime_minutes * 60
    progress_interval = settings.coding_agent_progress_interval_seconds

    changed_files_before = _get_git_changed_files(project_root)
    started_at = time.time()

    try:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            bufsize=1,  # line-buffered so output arrives as produced
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Coding agent command not found: {settings.coding_agent_command}"
        ) from error

    if cancellation_token is not None:
        cancellation_token.attach_process(process)

    # Read both streams concurrently to prevent pipe-buffer deadlock.
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _collect(stream: object, target: list[str]) -> None:
        for raw_line in stream:  # type: ignore[union-attr]
            line = raw_line.rstrip("\n")
            if line:
                target.append(line)

    stdout_thread = threading.Thread(target=_collect, args=(process.stdout, stdout_lines), daemon=True)
    stderr_thread = threading.Thread(target=_collect, args=(process.stderr, stderr_lines), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    cancelled = False
    last_update_at = started_at
    last_reported_count = 0

    try:
        while process.poll() is None:
            elapsed = time.time() - started_at

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
                new_lines = stdout_lines[last_reported_count:]
                last_reported_count = len(stdout_lines)
                _emit_progress_update(progress_callback, new_lines=new_lines, elapsed_seconds=elapsed)
                last_update_at = time.time()

            time.sleep(_POLL_INTERVAL_SECONDS)

    except (KeyboardInterrupt, SystemExit):
        process.kill()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        raise

    stdout_thread.join(timeout=10)
    stderr_thread.join(timeout=10)

    if not timed_out:
        process.wait()

    ended_at = time.time()
    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)
    changed_files_after = _get_git_changed_files(project_root)

    if cancelled:
        return CodingAgentResult(
            command=command,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            cancelled=True,
            message="The coding agent was cancelled by the operator.",
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=ended_at - started_at,
            changed_files_before=changed_files_before,
            changed_files_after=changed_files_after,
            changed_files_delta=_files_delta(changed_files_before, changed_files_after),
        )

    if timed_out:
        return CodingAgentResult(
            command=command,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            message=f"The coding agent timed out after {timeout_seconds} seconds.",
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=ended_at - started_at,
            changed_files_before=changed_files_before,
            changed_files_after=changed_files_after,
            changed_files_delta=_files_delta(changed_files_before, changed_files_after),
        )

    success = process.returncode == 0
    message = (
        f"The coding agent finished successfully. System exit code: {process.returncode}."
        if success
        else (
            f"The coding agent reported a failure. System exit code: {process.returncode}. "
            "It exited with a non-zero return code."
        )
    )

    return CodingAgentResult(
        command=command,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        message=message,
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=ended_at - started_at,
        success=success,
        changed_files_before=changed_files_before,
        changed_files_after=changed_files_after,
        changed_files_delta=_files_delta(changed_files_before, changed_files_after),
    )


def _get_git_changed_files(cwd: Path) -> tuple[str, ...]:
    """Return filenames reported by git status --short, or empty tuple on failure."""
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
            # git status --short: columns 0-1 are status, column 2 is space, 3+ is filename
            if len(line) > 3:
                files.append(line[3:].strip())
        return tuple(files)
    except Exception:
        return ()


def _files_delta(
    before: tuple[str, ...], after: tuple[str, ...]
) -> tuple[str, ...]:
    """Return files present in after but not in before — new changes from the agent."""
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
) -> None:
    elapsed_text = _format_elapsed_seconds(elapsed_seconds)
    if new_lines:
        preview = "\n".join(new_lines[-_PROGRESS_PREVIEW_LINES:])
        message = f"[{elapsed_text}]\n{preview}"
    else:
        message = f"[{elapsed_text}] Still running, no new output."

    logger.info(message)
    if progress_callback is None:
        return

    try:
        progress_callback(message)
    except Exception:
        logger.exception("Coding agent progress callback failed.")


def _format_elapsed_seconds(elapsed_seconds: float) -> str:
    total_seconds = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes == 0:
        return f"{seconds}s"
    return f"{minutes}m {seconds}s"
