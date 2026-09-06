"""Adapter-neutral coding-agent result contract (ATL-040).

Different coding-agent backends (Codex, Claude Code, ...) return plain prose
on stdout, not a shared structured payload. `normalize_coding_agent_result`
turns whatever raw result object a runner produced into one small,
adapter-neutral report so downstream code stops reaching into
runner-specific attributes directly. No adapter is required to emit JSON,
and no field is ever guessed: a partial or non-JSON result simply produces
empty/false/None for whatever it didn't state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The result-reporting skill asks every coding-agent handoff for exactly one
# final line in this form: "Validation: <command> — <passed|failed|not run>".
# Only that captured line is ever surfaced — never the surrounding stdout.
_VALIDATION_LINE_RE = re.compile(
    r"^\s*validation\s*:?\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
MAX_VALIDATION_CHARS = 200


@dataclass(frozen=True)
class CodingAgentReport:
    """Adapter-neutral coding-agent result. See ATL-040 for the field set."""

    success: bool
    returncode: int | None
    changed_files: tuple[str, ...]
    validation: str
    summary: str


def normalize_coding_agent_result(result: Any) -> CodingAgentReport:
    """Normalize a raw coding-agent runner result into the shared contract."""

    return CodingAgentReport(
        success=_result_succeeded(result),
        returncode=getattr(result, "returncode", None),
        changed_files=tuple(getattr(result, "changed_files_delta", ()) or ()),
        validation=_extract_validation(str(getattr(result, "stdout", "") or "")),
        summary=str(getattr(result, "message", "") or "").strip(),
    )


def _result_succeeded(result: Any) -> bool:
    """Prefer an explicit `success` field; fall back to return code otherwise."""

    explicit_success = getattr(result, "success", None)
    if explicit_success is not None:
        return bool(explicit_success)
    if getattr(result, "timed_out", False) or getattr(result, "cancelled", False):
        return False
    return getattr(result, "returncode", None) == 0


def _extract_validation(stdout: str) -> str:
    """Return the agent-declared validation line, or "" if it didn't give one."""

    match = _VALIDATION_LINE_RE.search(stdout)
    if not match:
        return ""
    value = " ".join(match.group("value").split())
    if not value:
        return ""
    if len(value) > MAX_VALIDATION_CHARS:
        value = value[: MAX_VALIDATION_CHARS - 1].rstrip() + "…"
    return value
