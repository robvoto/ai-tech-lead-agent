"""Relevance check: decide whether a request is actually AI Tech Lead's job.

AI Tech Lead is a coding/technical-lead specialist. This module answers one
narrow question before any other work happens: is this request coding or
technical-implementation work at all, or something ATL should not attempt?
"""

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
from .prompt_loader import ATL_RELEVANCE_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)
_RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "atl_relevant": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["atl_relevant", "reason"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RelevanceDecision:
    """Structured decision written back into LangGraph state."""

    is_atl_relevant: bool
    reason: str


def classify_request_relevance(request: str) -> RelevanceDecision:
    """Decide if a request is coding/technical work AI Tech Lead should handle.

    Fails open (treats the request as relevant) when AI is disabled or the
    call errors, so infrastructure problems never silently block real work.
    """

    normalized_request = request.strip()
    if not normalized_request:
        raise ValueError("Relevance check request cannot be empty.")

    settings = load_settings()
    if not settings.orchestrator_ai_enabled:
        return RelevanceDecision(is_atl_relevant=True, reason="Orchestrator AI disabled.")

    try:
        return _llm_relevance_decision(normalized_request, settings)
    except OrchestratorLlmError as error:
        logger.warning("Orchestrator AI relevance check unavailable: %s", error)
        return RelevanceDecision(
            is_atl_relevant=True,
            reason=f"Relevance check failed open (AI unavailable): {error}",
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Invalid orchestrator AI relevance response: %s", error)
        return RelevanceDecision(
            is_atl_relevant=True,
            reason=f"Relevance check failed open (invalid AI response): {error}",
        )


def _llm_relevance_decision(request: str, settings: AppSettings) -> RelevanceDecision:
    prompt = render_prompt(ATL_RELEVANCE_PROMPT_KEY, request=request)
    profile = resolve_profile_with_override("request_relevance", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="request_relevance",
        profile_name=profile.name,
    )
    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Orchestrator AI relevance response",
        schema_name="atl_relevance",
        schema=_RELEVANCE_SCHEMA,
        parse=_parse_relevance_payload,
        tier=profile.tier,
    )


def _parse_relevance_payload(payload: dict[str, Any]) -> RelevanceDecision:
    is_atl_relevant = payload["atl_relevant"]
    reason = str(payload["reason"]).strip()
    if not isinstance(is_atl_relevant, bool):
        raise ValueError("atl_relevant must be boolean")
    if not reason:
        raise ValueError("reason cannot be empty")
    return RelevanceDecision(is_atl_relevant=is_atl_relevant, reason=reason)
