"""Completion verifier — the AI Tech Lead decides whether finished coding-agent work is genuinely done."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings
from .implementation_review import BLOCKING_REVIEW_STATUSES
from .llm_json import call_llm_for_json
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
)
from .profile_override_store import resolve_profile_with_override
from .prompt_loader import COMPLETION_VERIFICATION_PROMPT_KEY, render_prompt
from .target_project_context import TargetProjectContext

logger = logging.getLogger(LOGGER_NAME)

STATUS_COMPLETE = "complete"
STATUS_CORRECTION_REQUIRED = "correction_required"
STATUS_HUMAN_VERIFICATION_REQUIRED = "human_verification_required"
_VALID_STATUSES = (STATUS_COMPLETE, STATUS_CORRECTION_REQUIRED, STATUS_HUMAN_VERIFICATION_REQUIRED)
_COMPLETION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(_VALID_STATUSES)},
        "reason": {"type": "string"},
        "correction": {"type": "string"},
    },
    "required": ["status", "reason", "correction"],
    "additionalProperties": False,
}


class CompletionVerificationUnavailable(RuntimeError):
    """Raised when completion cannot be verified automatically and a human must decide."""


@dataclass(frozen=True)
class CompletionVerificationDecision:
    status: str
    reason: str
    correction: str = ""


def verify_completion(
    *,
    bounded_request: str,
    formulated_task: str,
    brief: str,
    plan_text: str,
    acceptance_criteria: list[str],
    changed_files: tuple[str, ...],
    coding_agent_result: str,
    diff_summary: str = "",
    validation_command: str = "",
    validation_passed: bool | None = None,
    validation_output_tail: str = "",
    out_of_scope_paths: tuple[str, ...] = (),
    review_status: str = "",
    review_findings: tuple[str, ...] = (),
    target_project_context: TargetProjectContext | None = None,
    settings: AppSettings,
    prior_correction: str = "",
    project_guidance: list[str] | None = None,
) -> CompletionVerificationDecision:
    """Decide whether the coding agent's completed work satisfies the approved task.

    The AI Tech Lead decides completion from evidence it gathered itself
    (``diff_summary`` from the coding checkout, ``validation_passed`` from running
    the project's canonical validation command), not from the coding agent's own
    success prose. ``validation_passed`` is tri-state:

    - ``None`` — ATL had no command to run or could not execute one. Completion
      cannot be proven; raise ``CompletionVerificationUnavailable`` so the run
      routes to human verification. No LLM call is spent.
    - ``False`` — ATL ran the command and it failed. Return
      ``correction_required`` naming that single fix (the graph caps this at one
      cycle, then resolves to Failed).
    - ``True`` — the LLM weighs the real diff, validation output and
      out-of-scope list against the task, plan and acceptance criteria.

    ``project_guidance`` is the same bounded, already-selected notes used
    throughout this run — not re-selected here. A relevant violation is a reason
    for ``correction_required``, never a silent pass; but guidance can never
    excuse skipping the core safety/approval/evidence bar either.
    """

    if not settings.orchestrator_ai_enabled:
        logger.info("Completion verification: AI disabled, routing to human verification.")
        raise CompletionVerificationUnavailable(
            "AI review is disabled — human verification required."
        )

    if validation_passed is None:
        detail = validation_output_tail.strip() or "no validation result was captured"
        logger.warning(
            "Completion verification: no ATL validation result (%s) — human verification.",
            detail,
        )
        raise CompletionVerificationUnavailable(
            "The AI Tech Lead could not run the project's validation command "
            f"({detail}). A human must confirm completion or supply the command."
        )

    if validation_passed is False:
        detail = validation_output_tail.strip()
        reason = (
            f"The AI Tech Lead ran the project validation command `{validation_command}` "
            "and it failed."
        )
        if detail:
            reason = f"{reason}\nLast output:\n{detail}"
        logger.info("Completion verification: ATL validation failed -> correction_required.")
        return CompletionVerificationDecision(
            status=STATUS_CORRECTION_REQUIRED,
            reason=reason,
            correction=(
                f"Make the project validation command `{validation_command}` pass, "
                "then report it again."
            ),
        )

    if review_status in BLOCKING_REVIEW_STATUSES:
        # ATL-093: an independent review that could not run, failed, mutated the
        # checkout, or still had required findings after one correction cycle
        # cannot resolve to Complete — a human decides.
        findings_text = "; ".join(review_findings) if review_findings else "no findings recorded"
        logger.warning(
            "Completion verification: independent review %s (%s) — human verification.",
            review_status,
            findings_text,
        )
        raise CompletionVerificationUnavailable(
            f"The independent implementation review is '{review_status}' "
            f"({findings_text}). A human must decide completion."
        )

    try:
        return _llm_verify_completion(
            bounded_request=bounded_request,
            formulated_task=formulated_task,
            brief=brief,
            plan_text=plan_text,
            acceptance_criteria=acceptance_criteria,
            changed_files=changed_files,
            coding_agent_result=coding_agent_result,
            diff_summary=diff_summary,
            validation_command=validation_command,
            validation_output_tail=validation_output_tail,
            out_of_scope_paths=out_of_scope_paths,
            review_status=review_status,
            review_findings=review_findings,
            target_project_context=target_project_context,
            settings=settings,
            prior_correction=prior_correction,
            project_guidance=project_guidance or [],
        )
    except OrchestratorLlmError as error:
        logger.warning(
            "Completion verification LLM call failed: %s — routing to human verification.", error
        )
        raise CompletionVerificationUnavailable(
            f"Completion verification LLM unavailable: {error}"
        ) from error
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            "Completion verification returned invalid response: %s — routing to human verification.",
            error,
        )
        raise CompletionVerificationUnavailable(
            f"Completion verification returned invalid response: {error}"
        ) from error


def _llm_verify_completion(
    *,
    bounded_request: str,
    formulated_task: str,
    brief: str,
    plan_text: str,
    acceptance_criteria: list[str],
    changed_files: tuple[str, ...],
    coding_agent_result: str,
    diff_summary: str,
    validation_command: str,
    validation_output_tail: str,
    out_of_scope_paths: tuple[str, ...],
    review_status: str,
    review_findings: tuple[str, ...],
    target_project_context: TargetProjectContext | None,
    settings: AppSettings,
    prior_correction: str,
    project_guidance: list[str],
) -> CompletionVerificationDecision:
    project_text = "(not supplied)"
    if target_project_context is not None:
        project_text = (
            target_project_context.project_identity
            or target_project_context.project_root
            or "(not supplied)"
        )
    guidance_text = (
        "Target project's own guidance:\n" + "\n".join(f"- {g}" for g in project_guidance)
        if project_guidance
        else ""
    )
    validation_evidence = (
        f"The AI Tech Lead ran `{validation_command or '(command not determined)'}` "
        "and it PASSED.\nLast output:\n"
        + (validation_output_tail.strip() or "(no output captured)")
    )
    out_of_scope_text = (
        "\n".join(f"- {path}" for path in out_of_scope_paths)
        if out_of_scope_paths
        else "(none detected)"
    )
    if review_status == "clean":
        review_evidence = (
            "An independent second coding agent reviewed the change and found no "
            "issues that block completion."
            + (
                "\nAdvisory notes:\n" + "\n".join(f"- {f}" for f in review_findings)
                if review_findings
                else ""
            )
        )
    elif review_status == "skipped":
        review_evidence = (
            "No independent second-agent review was run (small, low-risk change)."
        )
    else:
        review_evidence = f"Independent second-agent review status: {review_status or 'not run'}."
    prompt = render_prompt(
        COMPLETION_VERIFICATION_PROMPT_KEY,
        bounded_request=bounded_request,
        formulated_task=formulated_task,
        brief=brief,
        project=project_text,
        plan_text=plan_text or "(no plan recorded)",
        acceptance_criteria="\n".join(f"- {item}" for item in acceptance_criteria) or "(none)",
        changed_files=", ".join(changed_files) or "(none detected)",
        diff_summary=diff_summary.strip() or "(no diff summary captured)",
        validation_evidence=validation_evidence,
        out_of_scope=out_of_scope_text,
        review_evidence=review_evidence,
        coding_agent_result=coding_agent_result or "(no completion report)",
        prior_correction=prior_correction or "(none — first check)",
        project_guidance=guidance_text,
    )
    profile = resolve_profile_with_override("completion_verification", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="completion_verification",
        profile_name=profile.name,
    )

    def _log_metric(result: Any) -> None:
        logger.info(
            "Completion verification LLM: in=%d out=%d total=%d cost_total=$%.5f",
            result.tokens_in,
            result.tokens_out,
            result.tokens_in + result.tokens_out,
            result.cost_usd,
        )

    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Completion verification response",
        schema_name="completion_verification",
        schema=_COMPLETION_SCHEMA,
        parse=_parse_verification_payload,
        on_result=_log_metric,
        tier=profile.tier,
    )


def _parse_verification_payload(payload: dict[str, Any]) -> CompletionVerificationDecision:
    status = str(payload["status"]).strip()
    reason = str(payload.get("reason", "")).strip()
    correction = str(payload.get("correction", "")).strip()

    if status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {_VALID_STATUSES}, got {status!r}")
    if not reason:
        raise ValueError("reason cannot be empty")
    if status == STATUS_CORRECTION_REQUIRED and not correction:
        raise ValueError("correction cannot be empty when status is correction_required")

    return CompletionVerificationDecision(status=status, reason=reason, correction=correction)
