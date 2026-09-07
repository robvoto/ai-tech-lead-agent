"""Independent second-agent implementation review (ATL-093).

ATL-039 gave the AI Tech Lead its own diff and validation evidence. This module
adds a second, genuinely different coding agent that reviews the actual change
read-only before completion, for tasks that are meaningful or risky. The
reviewer never edits, commits or pushes anything; a reviewer that mutates the
checkout is treated as a failed review.

The reviewer is dispatched through the same `run_coding_agent` machinery as the
read-only code-recon node, with `coding_agent_command`/`coding_agent_args`
swapped for the configured `review_agent_command`/`review_agent_args`. If no
distinct reviewer is configured, or its preflight fails, the review is
`unavailable` and the completion gate routes to human verification — never
Complete.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .app_settings import AppSettings
from .coding_agent_runner import run_coding_agent
from .logging_setup import LOGGER_NAME
from .prompt_loader import IMPLEMENTATION_REVIEW_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)

STATUS_SKIPPED = "skipped"
STATUS_CLEAN = "clean"
STATUS_FINDINGS = "findings"
STATUS_FINDINGS_UNRESOLVED = "findings_unresolved"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"
STATUS_MUTATED = "mutated"

# Completion cannot be Complete when the review is in one of these states.
BLOCKING_REVIEW_STATUSES = frozenset(
    {STATUS_UNAVAILABLE, STATUS_FAILED, STATUS_MUTATED, STATUS_FINDINGS_UNRESOLVED}
)

IMPLEMENTATION_REVIEW_MAX_CORRECTIONS = 1

_MAX_FINDINGS = 20
_MAX_FINDING_CHARS = 300
# Contract the review prompt asks for:
#   FINDING [required|advisory] <file-or-criterion>: <text>
#   REVIEW: pass | changes-required
_FINDING_RE = re.compile(
    r"^\s*FINDING\s*\[\s*(?P<severity>required|advisory)\s*\]\s*"
    r"(?P<target>[^:]{1,120}?)\s*:\s*(?P<text>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_VERDICT_RE = re.compile(
    r"^\s*REVIEW\s*:\s*(?P<verdict>pass|changes-required)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_RISK_KEYWORD_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ReviewFinding:
    severity: str  # "required" | "advisory"
    target: str
    text: str

    def render(self) -> str:
        return f"[{self.severity}] {self.target}: {self.text}"


@dataclass(frozen=True)
class ReviewOutcome:
    status: str
    findings: tuple[str, ...]
    correction: str
    performed_by: str


def review_required(
    *,
    coding_agent_tier: str,
    risk_level: str,
    risk_signal_text: str,
    settings: AppSettings,
) -> bool:
    """Decide whether a task needs the independent review.

    Required when the change is not clearly small (`coding_agent_tier` is
    `standard`/`deep`), or the risk review flagged it (`MEDIUM`/`HIGH`/`UNKNOWN`),
    or the task text / changed paths hit a configured risk keyword. The backlog
    `Size` field is intentionally not consulted — it is an unvalidated human
    estimate, and the plan-review tier already weighed it as a hint.
    """

    if not settings.implementation_review_enabled:
        return False
    if coding_agent_tier.strip().lower() in {"standard", "deep"}:
        return True
    if risk_level.strip().upper() in {"MEDIUM", "HIGH", "UNKNOWN"}:
        return True
    if matched_risk_keywords(risk_signal_text, settings):
        return True
    return False


def matched_risk_keywords(text: str, settings: AppSettings) -> list[str]:
    tokens = set(_RISK_KEYWORD_TOKEN_RE.findall(text.lower()))
    return [
        keyword
        for keyword in settings.implementation_review_risk_keywords
        if keyword.strip().lower() in tokens
    ]


def reviewer_available(settings: AppSettings) -> bool:
    """A usable independent reviewer is a non-empty command distinct from the implementer."""

    command = settings.review_agent_command.strip()
    return bool(command) and command != settings.coding_agent_command.strip()


def run_implementation_review(
    *,
    formulated_task: str,
    plan_text: str,
    acceptance_criteria: list[str],
    diff_summary: str,
    changed_files: tuple[str, ...],
    validation_command: str,
    validation_passed: bool | None,
    validation_output_tail: str,
    checkout_root: Path,
    settings: AppSettings,
) -> ReviewOutcome:
    """Dispatch the read-only reviewer and normalise its result."""

    if not reviewer_available(settings):
        logger.warning(
            "Implementation review: no independent reviewer configured "
            "(review_agent_command=%r, coding_agent_command=%r).",
            settings.review_agent_command,
            settings.coding_agent_command,
        )
        return ReviewOutcome(STATUS_UNAVAILABLE, (), "", "")

    reviewer_settings = replace(
        settings,
        coding_agent_command=settings.review_agent_command,
        coding_agent_args=list(settings.review_agent_args),
    )
    validation_line = _validation_line(
        validation_command, validation_passed, validation_output_tail
    )
    instruction = render_prompt(
        IMPLEMENTATION_REVIEW_PROMPT_KEY,
        formulated_task=formulated_task or "(not supplied)",
        plan_text=plan_text or "(no plan recorded)",
        acceptance_criteria="\n".join(f"- {item}" for item in acceptance_criteria) or "(none)",
        diff_summary=diff_summary.strip() or "(no diff summary captured)",
        changed_files=", ".join(changed_files) or "(none detected)",
        validation_evidence=validation_line,
    )

    try:
        # No sandbox flag is injected here: `-s read-only` is Codex-specific and
        # would break a non-Codex reviewer. Read-only enforcement for the
        # reviewer is the operator's `review_agent_args` (e.g. `-s read-only` for
        # Codex, `--permission-mode plan` for Claude Code) plus the prompt's
        # "do NOT edit" instruction; the post-run mutation check below is the
        # hard backstop that no reviewer output can bypass.
        result = run_coding_agent(
            agent_instruction=instruction,
            project_root=checkout_root,
            settings=reviewer_settings,
        )
    except RuntimeError as error:
        logger.warning("Implementation review: reviewer preflight failed: %s", error)
        return ReviewOutcome(STATUS_UNAVAILABLE, (), "", "")

    performed_by = settings.review_agent_command.strip()

    if result.changed_files_delta:
        # Hard backstop: a reviewer must never write. Any change on disk voids
        # the review regardless of what it reported.
        logger.warning(
            "Implementation review: reviewer mutated the checkout: %s",
            ", ".join(result.changed_files_delta[:10]),
        )
        return ReviewOutcome(STATUS_MUTATED, (), "", performed_by)

    if result.timed_out or result.cancelled or result.returncode not in (0, None):
        logger.warning(
            "Implementation review: reviewer run did not complete cleanly "
            "(returncode=%s timed_out=%s cancelled=%s).",
            result.returncode,
            result.timed_out,
            result.cancelled,
        )
        return ReviewOutcome(STATUS_FAILED, (), "", performed_by)

    return _parse_review_output(result.stdout or "", performed_by)


def _parse_review_output(stdout: str, performed_by: str) -> ReviewOutcome:
    verdict_match = _VERDICT_RE.search(stdout)
    if verdict_match is None:
        logger.warning("Implementation review: reviewer gave no 'REVIEW:' verdict line.")
        return ReviewOutcome(STATUS_FAILED, (), "", performed_by)

    findings: list[ReviewFinding] = []
    for match in _FINDING_RE.finditer(stdout):
        text = " ".join(match.group("text").split())[:_MAX_FINDING_CHARS]
        findings.append(
            ReviewFinding(
                severity=match.group("severity").lower(),
                target=" ".join(match.group("target").split()),
                text=text,
            )
        )
        if len(findings) >= _MAX_FINDINGS:
            break

    required = [f for f in findings if f.severity == "required"]
    verdict = verdict_match.group("verdict").lower()
    rendered = tuple(f.render() for f in findings)

    # The verdict line and the findings must agree. Either mismatch is an
    # unreliable review, so it fails closed to human verification — a `pass`
    # verdict must never let a `required` finding through as clean.
    if verdict == "pass" and required:
        logger.warning(
            "Implementation review: verdict 'pass' but %d required finding(s) present — "
            "failing closed.",
            len(required),
        )
        return ReviewOutcome(STATUS_FAILED, rendered, "", performed_by)

    if verdict == "changes-required" and not required:
        logger.warning(
            "Implementation review: verdict 'changes-required' with no required findings — "
            "treating as unparseable."
        )
        return ReviewOutcome(STATUS_FAILED, (), "", performed_by)

    if verdict == "changes-required":
        correction = "Address the required findings from the independent review:\n" + "\n".join(
            f"- {f.render()}" for f in required
        )
        return ReviewOutcome(STATUS_FINDINGS, rendered, correction, performed_by)

    return ReviewOutcome(STATUS_CLEAN, rendered, "", performed_by)


def _validation_line(
    command: str, passed: bool | None, output_tail: str
) -> str:
    if passed is None:
        return "The AI Tech Lead could not run a validation command for this project."
    state = "PASSED" if passed else "FAILED"
    tail = output_tail.strip()
    line = f"The AI Tech Lead ran `{command or '(command not stated)'}` and it {state}."
    return f"{line}\nLast output:\n{tail}" if tail else line
