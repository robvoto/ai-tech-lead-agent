"""Governed check for missing or conflicting project guidance.

Consumes project_guidance_discovery.py's bounded notes and asks the
orchestrator LLM one narrow question: does what was discovered (or its
absence) actually matter for doing this task safely and correctly — and if
so, is something missing, or does the discovered guidance contradict
itself? This never invents a rule and never merges a conflict quietly. The
answer is a small structured proposal (what's missing/conflicting, where
related rules already live in the project today, the smallest destination
for a fix, and why) — gated behind an explicit human-approval interrupt
(coding_workflow_graph.py's PROJECT_GUIDANCE_INTERRUPT) before that proposed
content is ever used for this run. No proposal is ever written back to the
target project's own files automatically; approval only permits using the
proposed content as this run's context, not committing it anywhere.

Fails closed like research_checker.py's knowledge-gap check: if the LLM is
disabled, errors, or returns an invalid response, this is treated as
"conflicting" with review required, never as silently sufficient — an
unverifiable guidance-coverage judgement is exactly the kind of uncertainty
AGENTS.md says to escalate rather than guess through.
"""

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
from .prompt_loader import PROJECT_GUIDANCE_GOVERNANCE_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)

_STATUSES = {"sufficient", "missing", "conflicting"}
_GUIDANCE_GOVERNANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["sufficient", "missing", "conflicting"]},
        "requires_review": {"type": "boolean"},
        "summary": {"type": "string"},
        "related_locations": {"type": "array", "items": {"type": "string"}},
        "proposed_change": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "status",
        "requires_review",
        "summary",
        "related_locations",
        "proposed_change",
        "reason",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GuidanceGovernanceDecision:
    """A small structured proposal — never an invented rule, never a silent merge."""

    status: str
    requires_review: bool
    summary: str
    related_locations: list[str]
    proposed_change: str
    reason: str


def review_project_guidance(
    request: str,
    project_guidance_notes: list[str],
    settings: AppSettings,
) -> GuidanceGovernanceDecision:
    """Decide whether discovered project guidance is sufficient, missing, or conflicting.

    ``requires_review`` is true whenever explicit human governance approval is
    needed before this run may use anything beyond what was already safely
    discovered: always for "conflicting" (never silently merged), and for
    "missing" only when the gap actually matters for safe/correct work — an
    absent project pack is not, by itself, a reason to block.
    """

    if not settings.orchestrator_ai_enabled:
        return _fail_closed("Orchestrator AI disabled; guidance coverage cannot be verified.")

    try:
        return _llm_review(request, project_guidance_notes, settings)
    except OrchestratorLlmError as error:
        logger.warning("Project guidance governance LLM unavailable: %s", error)
        return _fail_closed(f"Guidance governance check unavailable: {error}")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Project guidance governance response invalid: %s", error)
        return _fail_closed(f"Guidance governance response invalid: {error}")


def _fail_closed(reason: str) -> GuidanceGovernanceDecision:
    return GuidanceGovernanceDecision(
        status="conflicting",
        requires_review=True,
        summary=reason,
        related_locations=[],
        proposed_change="",
        reason=reason,
    )


def _llm_review(
    request: str,
    project_guidance_notes: list[str],
    settings: AppSettings,
) -> GuidanceGovernanceDecision:
    notes_text = "\n".join(f"- {note}" for note in project_guidance_notes) or "(none discovered)"
    prompt = render_prompt(
        PROJECT_GUIDANCE_GOVERNANCE_PROMPT_KEY,
        request=request,
        project_guidance=notes_text,
    )
    profile = resolve_profile_with_override("project_guidance_governance", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="project_guidance_governance",
        profile_name=profile.name,
    )

    def _log_metric(result: Any) -> None:
        logger.info(
            "Project guidance governance LLM: in=%d out=%d total=%d cost_total=$%.5f",
            result.tokens_in,
            result.tokens_out,
            result.tokens_in + result.tokens_out,
            result.cost_usd,
        )

    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Project guidance governance response",
        schema_name="project_guidance_governance",
        schema=_GUIDANCE_GOVERNANCE_SCHEMA,
        parse=_parse_decision,
        on_result=_log_metric,
        tier=profile.tier,
    )


def _parse_decision(payload: dict[str, Any]) -> GuidanceGovernanceDecision:
    status = str(payload.get("status", "")).strip().lower()
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {sorted(_STATUSES)}")
    requires_review = payload.get("requires_review", False)
    if not isinstance(requires_review, bool):
        raise ValueError("requires_review must be boolean")
    summary = str(payload.get("summary", "")).strip()
    if status != "sufficient" and not summary:
        raise ValueError("summary is required when status is not sufficient")
    related_locations_raw = payload.get("related_locations", [])
    if not isinstance(related_locations_raw, list):
        raise ValueError("related_locations must be a list")
    proposed_change = str(payload.get("proposed_change", "")).strip()
    reason = str(payload.get("reason", "")).strip()

    # Conflicting guidance is never silently merged — review is never optional
    # for it, regardless of what the model itself reported for requires_review.
    if status == "conflicting":
        requires_review = True
    if status == "sufficient":
        requires_review = False

    return GuidanceGovernanceDecision(
        status=status,
        requires_review=requires_review,
        summary=summary,
        related_locations=[
            str(location).strip() for location in related_locations_raw if str(location).strip()
        ],
        proposed_change=proposed_change,
        reason=reason,
    )
