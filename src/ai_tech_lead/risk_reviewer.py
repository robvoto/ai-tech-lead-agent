"""Risk review boundary for deciding whether a task needs human approval."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from .app_settings import AppSettings, load_settings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class RiskReviewDecision:
    """Structured decision written back into LangGraph state."""

    needs_approval: bool
    approval_reason: str


def review_task_risk(request: str) -> RiskReviewDecision:
    """Review a task request and decide whether human approval is needed."""

    normalized_request = request.strip()
    if not normalized_request:
        raise ValueError("Risk review request cannot be empty.")

    settings = load_settings()
    if not settings.orchestrator_ai_enabled:
        return _safe_default_decision(normalized_request, settings)

    try:
        return _llm_risk_decision(normalized_request, settings)
    except OrchestratorLlmError as error:
        logger.warning("Orchestrator AI risk review unavailable: %s", error)
        return _safe_default_decision(
            normalized_request,
            settings,
            prefix="Orchestrator AI risk review failed; approval required.",
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Invalid orchestrator AI risk review response: %s", error)
        return _safe_default_decision(
            normalized_request,
            settings,
            prefix="Orchestrator AI returned an invalid risk review; approval required.",
        )


def _llm_risk_decision(request: str, settings: AppSettings) -> RiskReviewDecision:
    prompt = _risk_review_prompt(request)
    result = call_orchestrator_llm(
        prompt=prompt,
        config=OrchestratorLlmConfig(
            model=settings.orchestrator_ai_model,
            max_output_tokens=settings.orchestrator_ai_max_output_tokens,
            timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        ),
    )
    payload = json.loads(result.text)
    return _parse_risk_payload(payload)


def _parse_risk_payload(payload: dict[str, Any]) -> RiskReviewDecision:
    needs_approval = payload["needs_approval"]
    reason = str(payload["reason"]).strip()
    confidence = float(payload.get("confidence", 0))

    if not isinstance(needs_approval, bool):
        raise ValueError("needs_approval must be boolean")
    if not reason:
        raise ValueError("reason cannot be empty")
    if confidence < 0.75:
        return RiskReviewDecision(
            needs_approval=True,
            approval_reason=(
                f"Low confidence orchestrator AI risk review ({confidence:.2f}); "
                f"approval required. Reason: {reason}"
            ),
        )

    return RiskReviewDecision(
        needs_approval=needs_approval,
        approval_reason=f"Orchestrator AI risk review: {reason}",
    )


def _safe_default_decision(
    request: str,
    settings: AppSettings,
    *,
    prefix: str | None = None,
) -> RiskReviewDecision:
    reason = settings.prompts["risk_review_reason_template"].format(request=request)
    if prefix:
        reason = f"{prefix}\n\n{reason}"
    return RiskReviewDecision(needs_approval=True, approval_reason=reason)


def _risk_review_prompt(request: str) -> str:
    return (
        "You are the AI Technical Lead Orchestrator risk reviewer. "
        "Return only valid JSON. Do not include Markdown. "
        "Decide whether this task requires human approval before a coding agent continues. "
        "Use this schema exactly: "
        "{\"needs_approval\": boolean, \"risk_level\": \"low|medium|high\", "
        "\"reason\": string, \"confidence\": number, \"recommended_action\": string}. "
        "Approval is required for destructive, broad, ambiguous, expensive, security-sensitive, "
        "architecture-changing, or externally connected work.\n\n"
        f"Task request:\n{request}"
    )
