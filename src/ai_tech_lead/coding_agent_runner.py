"""Controlled subprocess runner for real coding-agent execution.

The coding agent is launched as a separate CLI process with its working directory set to
the project root. Any file changes it makes are written directly to disk; the
current Python process must be restarted before it can import changed Python
code from those files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time

from ai_tech_lead.app_settings import AppSettings


@dataclass(frozen=True)
class CodingAgentResult:
    """Captured result from a coding-agent subprocess run."""

    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
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
                "Token usage: not available from the current Codex CLI runner."
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

    changed_files_before = _get_git_changed_files(project_root)
    started_at = time.time()

    try:
        completed_process = subprocess.run(
            command,
            cwd=project_root,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            f"Coding agent command not found: {settings.coding_agent_command}"
        ) from error
    except subprocess.TimeoutExpired as error:
        ended_at = time.time()
        changed_files_after = _get_git_changed_files(project_root)
        return CodingAgentResult(
            command=command,
            returncode=None,
            stdout=_clean_timeout_output(error.stdout),
            stderr=_clean_timeout_output(error.stderr),
            timed_out=True,
            message=f"The coding agent timed out after {timeout_seconds} seconds.",
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=ended_at - started_at,
            changed_files_before=changed_files_before,
            changed_files_after=changed_files_after,
            changed_files_delta=_files_delta(changed_files_before, changed_files_after),
        )

    ended_at = time.time()
    changed_files_after = _get_git_changed_files(project_root)
    success = completed_process.returncode == 0

    if success:
        message = f"The coding agent finished successfully. System exit code: {completed_process.returncode}."
    else:
        message = (
            f"The coding agent reported a failure. System exit code: {completed_process.returncode}. "
            "It exited with a non-zero return code."
        )

    return CodingAgentResult(
        command=command,
        returncode=completed_process.returncode,
        stdout=completed_process.stdout,
        stderr=completed_process.stderr,
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


def _clean_timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _display_command(command: list[str]) -> str:
    if not command:
        return "<not run>"
    if len(command) == 1:
        return command[0]
    return " ".join([*command[:-1], "<agent_instruction>"])
