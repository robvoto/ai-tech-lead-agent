"""Plan reviewer — orchestrator LLM decides whether the coding agent's plan is correct."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .prompt_loader import load_prompt
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)

logger = logging.getLogger(LOGGER_NAME)
PLAN_REVIEW_PROMPT = load_prompt("plan_review_prompt.md")


class PlanReviewUnavailable(RuntimeError):
    """Raised when the plan reviewer cannot evaluate the plan and human approval is required."""


@dataclass(frozen=True)
class PlanReviewDecision:
    approved: bool
    reason: str
    correction: str


def review_plan(
    formulated_task: str,
    plan_text: str,
    settings: AppSettings,
) -> PlanReviewDecision:
    """Decide whether the coding agent's plan correctly addresses the task."""

    if not settings.orchestrator_ai_enabled:
        logger.info("Plan review: AI disabled, routing to human approval.")
        raise PlanReviewUnavailable("AI review is disabled — human approval required.")

    if not plan_text.strip():
        logger.warning("Plan review: plan text is empty, rejecting.")
        return PlanReviewDecision(
            approved=False,
            reason="The coding agent returned an empty plan.",
            correction="Please output a brief implementation plan as described.",
        )

    try:
        return _llm_review_plan(formulated_task, plan_text, settings)
    except OrchestratorLlmError as error:
        logger.warning("Plan review LLM call failed: %s — routing to human approval.", error)
        raise PlanReviewUnavailable(f"Plan review LLM unavailable: {error}") from error
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Plan review returned invalid response: %s — routing to human approval.", error)
        raise PlanReviewUnavailable(f"Plan review returned invalid response: {error}") from error


def _llm_review_plan(
    formulated_task: str,
    plan_text: str,
    settings: AppSettings,
) -> PlanReviewDecision:
    prompt = (
        PLAN_REVIEW_PROMPT
        .replace("{formulated_task}", formulated_task)
        .replace("{plan_text}", plan_text)
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
        "Plan review LLM: in=%d out=%d cost=$%.5f",
        result.tokens_in,
        result.tokens_out,
        result.cost_usd,
    )
    payload = json.loads(result.text)
    return _parse_review_payload(payload)


def _parse_review_payload(payload: dict[str, Any]) -> PlanReviewDecision:
    approved = payload["approved"]
    reason = str(payload.get("reason", "")).strip()
    correction = str(payload.get("correction", "")).strip()

    if not isinstance(approved, bool):
        raise ValueError("approved must be boolean")
    if not reason:
        raise ValueError("reason cannot be empty")

    return PlanReviewDecision(approved=approved, reason=reason, correction=correction)
