"""Controlled subprocess runner for real coding-agent execution.

Codex is launched as a separate CLI process with its working directory set to
the project root. Any file changes it makes are written directly to disk; the
current Python process must be restarted before it can import changed Python
code from those files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

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

    def summary(self) -> str:
        """Return a concise, log-friendly summary of the subprocess outcome."""

        command_text = _display_command(self.command)
        parts = [
            self.message or "Coding agent subprocess completed.",
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
    """Run Codex CLI for real when settings explicitly allow execution."""

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
        return CodingAgentResult(
            command=command,
            returncode=None,
            stdout=_clean_timeout_output(error.stdout),
            stderr=_clean_timeout_output(error.stderr),
            timed_out=True,
            message=f"Coding agent timed out after {timeout_seconds} seconds.",
        )

    message = "Coding agent subprocess completed."
    if completed_process.returncode != 0:
        message = "Coding agent subprocess exited with a non-zero return code."

    return CodingAgentResult(
        command=command,
        returncode=completed_process.returncode,
        stdout=completed_process.stdout,
        stderr=completed_process.stderr,
        message=message,
    )


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
