"""Check research evidence requirements before a task proceeds.

The task is asked to name one specific external knowledge gap, not to rate
complexity — complexity and "do we lack a specific fact" are different
questions, and a task can be large or architecturally significant while
needing no external research at all. The LLM sees bounded code context from
the watched directories alongside the request, so a gap already answered by
existing code is not flagged. If no gap is named, no research is needed
regardless of task size. If a gap is named, the local docs/research indexes
are searched for that exact question; the caller must pause for human
approval before fetching online docs only if that search does not meet the
configured local threshold.
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
from .prompt_loader import RESEARCH_KNOWLEDGE_GAP_PROMPT_KEY, render_prompt
from .research_code_context import collect_code_context, format_code_context_for_prompt
from .research_sources import collect_local_research_sources

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class ResearchCheckResult:
    """Decision on whether the task needs research evidence before proceeding."""

    has_gap: bool
    gap_question: str
    sources_found: int
    online_research_needed: bool
    gap_reason: str
    usable_source_titles: list[str]
    usable_source_locations: list[str]
    usable_source_summaries: list[str]


def check_research_requirements(
    request: str,
    settings: AppSettings,
    *,
    code_context_root: str | None = None,
) -> ResearchCheckResult:
    """Identify a knowledge gap, if any, and check local research evidence adequacy.

    `code_context_root` scopes the bounded code-context lookup to the resolved
    target project when the task is about a different project than this one —
    the local docs/research cache lookup still always uses `settings.project_root`
    (AI Tech Lead's own shared knowledge base), only the code-context scan moves.
    Returns online_research_needed=True if a knowledge gap was identified and
    the local docs/research indexes do not meet the configured minimum source
    count for that gap question.
    """

    if not settings.orchestrator_ai_enabled:
        logger.warning(
            "[LEARN] Research check: AI disabled, treating task as "
            "uncertain and requiring approval."
        )
        logger.info(
            "[LEARN] Research gate summary: has_gap=yes local_sources=0 "
            "min_required=%d online_approval_needed=yes",
            settings.research_min_local_sources,
        )
        return ResearchCheckResult(
            has_gap=True,
            gap_question=request,
            sources_found=0,
            online_research_needed=True,
            gap_reason="AI knowledge-gap check disabled; requiring human approval.",
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )

    code_context_text = _collect_code_context_text(request, settings, code_context_root)

    try:
        has_gap, gap_question, reason = _llm_check_knowledge_gap(
            request, settings, code_context_text
        )
    except OrchestratorLlmError as error:
        logger.warning(
            "Research knowledge-gap check LLM unavailable: %s — requiring human approval.",
            error,
        )
        return ResearchCheckResult(
            has_gap=True,
            gap_question=request,
            sources_found=0,
            online_research_needed=True,
            gap_reason="Knowledge-gap check unavailable; requiring human approval.",
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            "Research knowledge-gap response invalid: %s — requiring human approval.",
            error,
        )
        return ResearchCheckResult(
            has_gap=True,
            gap_question=request,
            sources_found=0,
            online_research_needed=True,
            gap_reason="Knowledge-gap check response invalid; requiring human approval.",
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )

    logger.info(
        "[LEARN] Research knowledge-gap verdict: has_gap=%s gap_question=%s reason=%s",
        "yes" if has_gap else "no",
        gap_question,
        reason,
    )

    if not has_gap:
        logger.info(
            "[LEARN] Research gate summary: has_gap=no local_sources=0 "
            "min_required=%d online_approval_needed=no",
            settings.research_min_local_sources,
        )
        return ResearchCheckResult(
            has_gap=False,
            gap_question="",
            sources_found=0,
            online_research_needed=False,
            gap_reason=reason,
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )

    entries = collect_local_research_sources(gap_question, settings)
    sources_found = len(entries)
    usable_titles = [entry.title for entry in entries]
    usable_locations = [entry.location for entry in entries]
    usable_summaries = [entry.summary for entry in entries]
    online_research_needed = sources_found < settings.research_min_local_sources

    logger.info("[LEARN] Local research sources found for gap question: %d", sources_found)
    logger.info(
        "[LEARN] Online research approval needed: %s", "yes" if online_research_needed else "no"
    )
    logger.info(
        "[LEARN] Research gate summary: has_gap=yes local_sources=%d "
        "min_required=%d online_approval_needed=%s",
        sources_found,
        settings.research_min_local_sources,
        "yes" if online_research_needed else "no",
    )

    return ResearchCheckResult(
        has_gap=True,
        gap_question=gap_question,
        sources_found=sources_found,
        online_research_needed=online_research_needed,
        gap_reason=reason,
        usable_source_titles=usable_titles,
        usable_source_locations=usable_locations,
        usable_source_summaries=usable_summaries,
    )


def _collect_code_context_text(
    request: str,
    settings: AppSettings,
    code_context_root: str | None,
) -> str:
    try:
        sources = collect_code_context(request, settings, project_root_override=code_context_root)
    except OSError as error:
        logger.warning("[LEARN] Code context collection failed: %s — continuing with none.", error)
        return "(none)"
    return format_code_context_for_prompt(sources)


def _llm_check_knowledge_gap(
    request: str,
    settings: AppSettings,
    code_context_text: str,
) -> tuple[bool, str, str]:
    prompt = render_prompt(
        RESEARCH_KNOWLEDGE_GAP_PROMPT_KEY,
        request=request,
        code_context=code_context_text,
    )
    profile = resolve_profile_with_override("research_knowledge_gap_check", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="research_knowledge_gap_check",
        profile_name=profile.name,
    )

    def _log_metric(result: Any) -> None:
        logger.info(
            "Research knowledge-gap LLM: in=%d out=%d total=%d cost_total=$%.5f",
            result.tokens_in,
            result.tokens_out,
            result.tokens_in + result.tokens_out,
            result.cost_usd,
        )

    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Research knowledge-gap response",
        parse=_parse_knowledge_gap_payload,
        on_result=_log_metric,
        tier=profile.tier,
    )


def _parse_knowledge_gap_payload(payload: dict[str, Any]) -> tuple[bool, str, str]:
    has_gap = payload["has_gap"]
    if not isinstance(has_gap, bool):
        raise ValueError("has_gap must be boolean")
    gap_question = str(payload.get("gap_question", "")).strip()
    if has_gap and not gap_question:
        raise ValueError("gap_question must be non-empty when has_gap is true")
    reason = str(payload.get("reason", "")).strip() or "No reason provided."
    return has_gap, gap_question, reason
