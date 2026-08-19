"""Plan reviewer — orchestrator LLM decides whether the coding agent's plan is correct."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings
from .llm_json import call_llm_for_json
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import OrchestratorLlmConfig, OrchestratorLlmError
from .profile_override_store import resolve_profile_with_override
from .prompt_loader import PLAN_REVIEW_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)
MAX_PLAN_REVIEW_WORDS = 120
MAX_PLAN_REVIEW_LINES = 5
_PLAN_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "reason": {"type": "string"},
        "correction": {"type": "string"},
    },
    "required": ["approved", "reason", "correction"],
    "additionalProperties": False,
}


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
    agent_error: str = "",
    project_guidance: list[str] | None = None,
) -> PlanReviewDecision:
    """Decide whether the coding agent's plan correctly addresses the task.

    ``project_guidance`` is the same bounded, already-selected notes Tech Lead
    Analysis used (see project_guidance_discovery.py) — not re-selected here.
    The reviewer checks the plan against it (implementation ownership, required
    validation, established patterns) in addition to the core small/specific/
    testable criteria; guidance may add or narrow project-local expectations
    but must never be treated as a reason to approve a plan that skips core
    safety, approval, evidence, or stop rules.
    """

    if not settings.orchestrator_ai_enabled:
        logger.info("Plan review: AI disabled, routing to human approval.")
        raise PlanReviewUnavailable("AI review is disabled — human approval required.")

    if not plan_text.strip():
        logger.warning("Plan review: plan text is empty, rejecting.")
        reason = "The coding agent returned an empty plan."
        if agent_error:
            preview = agent_error[:300].strip()
            reason = f"{reason} Agent output: {preview}"
        return PlanReviewDecision(
            approved=False,
            reason=reason,
            correction="Please output a brief implementation plan as described.",
        )

    if _plan_is_too_verbose(plan_text):
        logger.warning(
            "Plan review: plan text is too verbose, rejecting before LLM review."
        )
        return PlanReviewDecision(
            approved=False,
            reason="The coding agent's plan is too long.",
            correction="Please rewrite it as 3 to 5 short bullets and one Done when line.",
        )

    try:
        return _llm_review_plan(formulated_task, plan_text, settings, project_guidance or [])
    except OrchestratorLlmError as error:
        logger.warning("Plan review LLM call failed: %s — routing to human approval.", error)
        raise PlanReviewUnavailable(f"Plan review LLM unavailable: {error}") from error
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            "Plan review returned invalid response: %s — routing to human approval.", error
        )
        raise PlanReviewUnavailable(f"Plan review returned invalid response: {error}") from error


def _llm_review_plan(
    formulated_task: str,
    plan_text: str,
    settings: AppSettings,
    project_guidance: list[str],
) -> PlanReviewDecision:
    guidance_text = (
        "Target project's own guidance:\n" + "\n".join(f"- {g}" for g in project_guidance)
        if project_guidance
        else ""
    )
    prompt = render_prompt(
        PLAN_REVIEW_PROMPT_KEY,
        formulated_task=formulated_task,
        plan_text=plan_text,
        project_guidance=guidance_text,
    )
    profile = resolve_profile_with_override("plan_review", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="plan_review",
        profile_name=profile.name,
    )

    def _log_metric(result: Any) -> None:
        logger.info(
            "Plan review LLM: in=%d out=%d total=%d cost_total=$%.5f",
            result.tokens_in,
            result.tokens_out,
            result.tokens_in + result.tokens_out,
            result.cost_usd,
        )

    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Plan review response",
        schema_name="plan_review",
        schema=_PLAN_REVIEW_SCHEMA,
        parse=_parse_review_payload,
        on_result=_log_metric,
        tier=profile.tier,
    )


def _parse_review_payload(payload: dict[str, Any]) -> PlanReviewDecision:
    approved = payload["approved"]
    reason = str(payload.get("reason", "")).strip()
    correction = str(payload.get("correction", "")).strip()

    if not isinstance(approved, bool):
        raise ValueError("approved must be boolean")
    if not reason:
        raise ValueError("reason cannot be empty")

    return PlanReviewDecision(approved=approved, reason=reason, correction=correction)


def _plan_is_too_verbose(plan_text: str) -> bool:
    lines = [line.strip() for line in plan_text.splitlines() if line.strip()]
    return len(lines) > MAX_PLAN_REVIEW_LINES or len(plan_text.split()) > MAX_PLAN_REVIEW_WORDS
