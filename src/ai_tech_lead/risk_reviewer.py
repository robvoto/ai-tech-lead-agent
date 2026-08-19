"""Risk review boundary for deciding whether a task needs human approval."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings, load_settings
from .llm_json import call_llm_for_json
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
)
from .profile_override_store import resolve_profile_with_override
from .prompt_loader import (
    RISK_REVIEW_PROMPT_KEY,
    RISK_REVIEW_REASON_PROMPT_KEY,
    render_prompt,
)

logger = logging.getLogger(LOGGER_NAME)


RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
_RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_approval": {"type": "boolean"},
        "risk_level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["needs_approval", "risk_level", "reason", "confidence"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RiskReviewDecision:
    """Structured decision written back into LangGraph state."""

    needs_approval: bool
    approval_reason: str
    risk_level: str = "UNKNOWN"  # LOW, MEDIUM, HIGH, UNKNOWN


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
    profile = resolve_profile_with_override("risk_review", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="risk_review",
        profile_name=profile.name,
    )
    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Orchestrator AI risk review response",
        schema_name="risk_review",
        schema=_RISK_SCHEMA,
        parse=_parse_risk_payload,
        tier=profile.tier,
    )


def _parse_risk_payload(payload: dict[str, Any]) -> RiskReviewDecision:
    needs_approval = payload["needs_approval"]
    reason = str(payload["reason"]).strip()
    confidence = float(payload.get("confidence", 0))
    raw_level = str(payload.get("risk_level", "")).strip().upper()
    risk_level = raw_level if raw_level in RISK_LEVELS else "UNKNOWN"

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
            risk_level="UNKNOWN",
        )

    return RiskReviewDecision(
        needs_approval=needs_approval,
        approval_reason=f"Orchestrator AI risk review: {reason}",
        risk_level=risk_level,
    )


def _safe_default_decision(
    request: str,
    settings: AppSettings,
    *,
    prefix: str | None = None,
) -> RiskReviewDecision:
    reason = render_prompt(RISK_REVIEW_REASON_PROMPT_KEY, request=request)
    if prefix:
        reason = f"{prefix}\n\n{reason}"
    return RiskReviewDecision(needs_approval=True, approval_reason=reason, risk_level="UNKNOWN")


def _risk_review_prompt(request: str) -> str:
    return render_prompt(RISK_REVIEW_PROMPT_KEY, request=request)
