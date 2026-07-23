"""Check research evidence requirements before complex tasks proceed.

Complexity is assessed by an LLM call when AI is enabled.
Relevant local docs and cached research notes are scanned for usable sources.
If the task is complex and the configured local threshold is not met,
the caller must pause for human approval before continuing.
"""

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
from .prompt_loader import RESEARCH_COMPLEXITY_PROMPT_KEY, render_prompt
from .research_sources import collect_local_research_sources

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class ResearchCheckResult:
    """Decision on whether the task needs research evidence before proceeding."""

    is_complex: bool
    sources_found: int
    online_research_needed: bool
    complexity_reason: str
    usable_source_titles: list[str]
    usable_source_locations: list[str]
    usable_source_summaries: list[str]


def check_research_requirements(
    request: str,
    settings: AppSettings,
) -> ResearchCheckResult:
    """Assess complexity and check local research evidence adequacy.

    Returns online_research_needed=True if the task is complex and the local
    docs/research indexes do not meet the configured minimum source count.
    """

    if not settings.orchestrator_ai_enabled:
        logger.warning(
            "[LEARN] Research check: AI disabled, treating task as "
            "uncertain and requiring approval."
        )
        logger.info(
            "[LEARN] Research gate summary: complex=yes local_sources=0 "
            "min_required=%d online_approval_needed=yes",
            settings.research_min_local_sources,
        )
        return ResearchCheckResult(
            is_complex=True,
            sources_found=0,
            online_research_needed=True,
            complexity_reason="AI complexity check disabled; requiring human approval.",
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )

    try:
        is_complex, reason = _llm_check_complexity(request, settings)
    except OrchestratorLlmError as error:
        logger.warning(
            "Research complexity check LLM unavailable: %s — requiring human approval.",
            error,
        )
        return ResearchCheckResult(
            is_complex=True,
            sources_found=0,
            online_research_needed=True,
            complexity_reason="Complexity check unavailable; requiring human approval.",
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            "Research complexity response invalid: %s — requiring human approval.",
            error,
        )
        return ResearchCheckResult(
            is_complex=True,
            sources_found=0,
            online_research_needed=True,
            complexity_reason="Complexity check response invalid; requiring human approval.",
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )

    logger.info(
        "[LEARN] Research complexity verdict: complex=%s reason=%s",
        "yes" if is_complex else "no",
        reason,
    )

    if not is_complex:
        logger.info(
            "[LEARN] Research gate summary: complex=no local_sources=0 "
            "min_required=%d online_approval_needed=no",
            settings.research_min_local_sources,
        )
        return ResearchCheckResult(
            is_complex=False,
            sources_found=0,
            online_research_needed=False,
            complexity_reason=reason,
            usable_source_titles=[],
            usable_source_locations=[],
            usable_source_summaries=[],
        )

    entries = collect_local_research_sources(request, settings)
    sources_found = len(entries)
    usable_titles = [entry.title for entry in entries]
    usable_locations = [entry.location for entry in entries]
    usable_summaries = [entry.summary for entry in entries]
    online_research_needed = sources_found < settings.research_min_local_sources

    logger.info("[LEARN] Local research sources found: %d", sources_found)
    logger.info(
        "[LEARN] Online research approval needed: %s", "yes" if online_research_needed else "no"
    )
    logger.info(
        "[LEARN] Research gate summary: complex=yes local_sources=%d "
        "min_required=%d online_approval_needed=%s",
        sources_found,
        settings.research_min_local_sources,
        "yes" if online_research_needed else "no",
    )

    return ResearchCheckResult(
        is_complex=True,
        sources_found=sources_found,
        online_research_needed=online_research_needed,
        complexity_reason=reason,
        usable_source_titles=usable_titles,
        usable_source_locations=usable_locations,
        usable_source_summaries=usable_summaries,
    )


_COMPLEXITY_CHECK_ATTEMPTS = 2


def _llm_check_complexity(
    request: str,
    settings: AppSettings,
) -> tuple[bool, str]:
    prompt = render_prompt(RESEARCH_COMPLEXITY_PROMPT_KEY, request=request)
    config = OrchestratorLlmConfig(
        model=settings.orchestrator_ai_model,
        max_output_tokens=settings.orchestrator_ai_max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
    )

    last_error: Exception = ValueError("complexity check produced no attempts")
    for attempt in range(1, _COMPLEXITY_CHECK_ATTEMPTS + 1):
        result = call_orchestrator_llm(prompt=prompt, config=config)
        logger.info(
            "Research complexity LLM: in=%d out=%d total=%d cost_total=$%.5f",
            result.tokens_in,
            result.tokens_out,
            result.tokens_in + result.tokens_out,
            result.cost_usd,
        )
        try:
            payload = _load_complexity_json(result.text)
            return _parse_complexity_payload(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            logger.warning(
                "Research complexity response invalid on attempt %d/%d: %s — raw response: %r",
                attempt,
                _COMPLEXITY_CHECK_ATTEMPTS,
                error,
                result.text,
            )

    raise last_error


def _load_complexity_json(raw_text: str) -> dict[str, Any]:
    """Parse plain JSON or a single Markdown-fenced JSON object."""

    text = raw_text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise TypeError("complexity response must be a JSON object")
    return payload


def _parse_complexity_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    is_complex = payload["is_complex"]
    if not isinstance(is_complex, bool):
        raise ValueError("is_complex must be boolean")
    reason = str(payload.get("reason", "")).strip() or "No reason provided."
    return is_complex, reason
