"""Completion verifier — the AI Tech Lead decides whether finished coding-agent work is genuinely done."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)
from .prompt_loader import COMPLETION_VERIFICATION_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)

STATUS_COMPLETE = "complete"
STATUS_CORRECTION_REQUIRED = "correction_required"
STATUS_HUMAN_VERIFICATION_REQUIRED = "human_verification_required"
_VALID_STATUSES = (STATUS_COMPLETE, STATUS_CORRECTION_REQUIRED, STATUS_HUMAN_VERIFICATION_REQUIRED)


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
    settings: AppSettings,
    prior_correction: str = "",
) -> CompletionVerificationDecision:
    """Decide whether the coding agent's completed work satisfies the approved task."""

    if not settings.orchestrator_ai_enabled:
        logger.info("Completion verification: AI disabled, routing to human verification.")
        raise CompletionVerificationUnavailable(
            "AI review is disabled — human verification required."
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
            settings=settings,
            prior_correction=prior_correction,
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
    settings: AppSettings,
    prior_correction: str,
) -> CompletionVerificationDecision:
    prompt = render_prompt(
        COMPLETION_VERIFICATION_PROMPT_KEY,
        bounded_request=bounded_request,
        formulated_task=formulated_task,
        brief=brief,
        plan_text=plan_text or "(no plan recorded)",
        acceptance_criteria="\n".join(f"- {item}" for item in acceptance_criteria) or "(none)",
        changed_files=", ".join(changed_files) or "(none detected)",
        coding_agent_result=coding_agent_result or "(no completion report)",
        prior_correction=prior_correction or "(none — first check)",
    )
    result = call_orchestrator_llm(
        prompt=prompt,
        config=OrchestratorLlmConfig(
            model=settings.orchestrator_ai_model,
            max_output_tokens=settings.orchestrator_ai_max_output_tokens,
            timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        ),
    )
    logger.info(
        "Completion verification LLM: in=%d out=%d total=%d cost_total=$%.5f",
        result.tokens_in,
        result.tokens_out,
        result.tokens_in + result.tokens_out,
        result.cost_usd,
    )
    payload = json.loads(result.text)
    return _parse_verification_payload(payload)


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
