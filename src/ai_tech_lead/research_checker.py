"""Check research evidence requirements before complex tasks proceed.

Complexity is assessed by an LLM call (when AI is enabled).
Local cache is scanned for usable sources.
If the task is complex and the local cache has fewer than 2 sources,
the caller must pause for human approval before continuing.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)
from .prompt_loader import load_prompt
from .research_cache import ResearchCacheEntry, load_research_cache_entries

RESEARCH_COMPLEXITY_PROMPT = load_prompt("research_complexity_prompt.md")
MINIMUM_LOCAL_SOURCES = 2

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class ResearchCheckResult:
    """Decision on whether the task needs research evidence before proceeding."""

    is_complex: bool
    sources_found: int
    online_research_needed: bool
    complexity_reason: str
    usable_source_titles: list[str]


def check_research_requirements(
    request: str,
    settings: AppSettings,
) -> ResearchCheckResult:
    """Assess complexity and check local research cache adequacy.

    Returns online_research_needed=True if the task is complex and the local
    cache has fewer than MINIMUM_LOCAL_SOURCES entries.
    """

    if not settings.orchestrator_ai_enabled:
        logger.info("[LEARN] Research check: AI disabled, treating task as simple.")
        return ResearchCheckResult(
            is_complex=False,
            sources_found=0,
            online_research_needed=False,
            complexity_reason="AI complexity check disabled.",
            usable_source_titles=[],
        )

    try:
        is_complex, reason = _llm_check_complexity(request, settings)
    except OrchestratorLlmError as error:
        logger.warning("Research complexity check LLM unavailable: %s — treating as simple.", error)
        return ResearchCheckResult(
            is_complex=False,
            sources_found=0,
            online_research_needed=False,
            complexity_reason="Complexity check unavailable.",
            usable_source_titles=[],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Research complexity response invalid: %s — treating as simple.", error)
        return ResearchCheckResult(
            is_complex=False,
            sources_found=0,
            online_research_needed=False,
            complexity_reason="Complexity check response invalid.",
            usable_source_titles=[],
        )

    logger.info(
        "[LEARN] Research evidence required: %s — %s",
        "yes" if is_complex else "no",
        reason,
    )

    if not is_complex:
        return ResearchCheckResult(
            is_complex=False,
            sources_found=0,
            online_research_needed=False,
            complexity_reason=reason,
            usable_source_titles=[],
        )

    entries = _load_cache_entries()
    sources_found = len(entries)
    usable_titles = [entry.title for entry in entries]
    online_research_needed = sources_found < MINIMUM_LOCAL_SOURCES

    logger.info("[LEARN] Local research sources found: %d", sources_found)
    logger.info("[LEARN] Online research approval needed: %s", "yes" if online_research_needed else "no")

    return ResearchCheckResult(
        is_complex=True,
        sources_found=sources_found,
        online_research_needed=online_research_needed,
        complexity_reason=reason,
        usable_source_titles=usable_titles,
    )


def _llm_check_complexity(
    request: str,
    settings: AppSettings,
) -> tuple[bool, str]:
    prompt = RESEARCH_COMPLEXITY_PROMPT.replace("{request}", request)
    result = call_orchestrator_llm(
        prompt=prompt,
        config=OrchestratorLlmConfig(
            model=settings.orchestrator_ai_model,
            max_output_tokens=settings.orchestrator_ai_max_output_tokens,
            timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        ),
    )
    logger.info(
        "Research complexity LLM: in=%d out=%d cost=$%.5f",
        result.tokens_in,
        result.tokens_out,
        result.cost_usd,
    )
    payload = json.loads(result.text)
    return _parse_complexity_payload(payload)


def _parse_complexity_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    is_complex = payload["is_complex"]
    if not isinstance(is_complex, bool):
        raise ValueError("is_complex must be boolean")
    reason = str(payload.get("reason", "")).strip() or "No reason provided."
    return is_complex, reason


def _load_cache_entries() -> list[ResearchCacheEntry]:
    try:
        return load_research_cache_entries()
    except FileNotFoundError:
        return []
