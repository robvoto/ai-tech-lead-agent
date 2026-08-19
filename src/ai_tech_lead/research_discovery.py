"""Runtime discovery of a candidate official documentation source.

Only runs when explicitly enabled (`research_discovery_enabled`). A
discovered candidate is never trusted outright: every candidate must pass
the same outbound-URL safety guard used before any fetch
(`research_sources.validate_outbound_research_url`), and the graph's human
approval step names the specific URL being approved — approving one
candidate never approves fetching anything else. Discovered URLs are never
written back into the permanent approved-source configuration; expanding
that stays a separate, manual decision.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_web_search,
)
from .profile_override_store import resolve_profile_with_override
from .research_policy import is_url_on_trusted_domain
from .research_sources import ResearchUrlSafetyError, validate_outbound_research_url

logger = logging.getLogger(LOGGER_NAME)

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")


@dataclass(frozen=True)
class DiscoveredSource:
    """One candidate official source found by runtime discovery."""

    url: str
    title: str


def discover_official_source(
    gap_question: str,
    settings: AppSettings,
    *,
    trusted_domains: tuple[str, ...] | list[str] | None = None,
) -> DiscoveredSource | None:
    """Discover a candidate official documentation URL for a knowledge gap.

    Returns None whenever discovery is disabled, the search call fails, or no
    candidate survives the outbound-URL safety guard. Discovery failing must
    never fail the graph — it just means falling back to the existing bounded
    registry question.
    """

    if not settings.research_discovery_enabled:
        return None

    profile = resolve_profile_with_override("research_discovery", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.research_discovery_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="research_discovery",
        profile_name=profile.name,
    )

    try:
        result = call_orchestrator_web_search(
            query=(
                "Find the single official or primary documentation page that answers this "
                f"question: {gap_question}. "
                + (
                    "Only return a page from one of these trusted domains: "
                    + ", ".join(trusted_domains)
                    if trusted_domains
                    else "Prefer the technology owner's official documentation."
                )
            ),
            config=config,
        )
    except OrchestratorLlmError as error:
        logger.warning("[LEARN] Research source discovery call failed: %s", error)
        return None

    candidates = _extract_citation_urls(result.data)
    if not candidates:
        candidates = _extract_urls_from_text(result.text)

    for url, title in candidates[: settings.research_discovery_max_candidates]:
        if trusted_domains and not is_url_on_trusted_domain(url, trusted_domains):
            logger.warning(
                "[LEARN] Discovered source candidate rejected by trusted-domain policy: %s",
                url,
            )
            continue
        try:
            validate_outbound_research_url(url)
        except ResearchUrlSafetyError as error:
            logger.warning(
                "[LEARN] Discovered source candidate rejected by safety guard: %s (%s)",
                url,
                error,
            )
            continue
        logger.info("[LEARN] Research source discovery found a candidate: %s (%s)", url, title)
        return DiscoveredSource(url=url, title=title or url)

    logger.info("[LEARN] Research source discovery found no safe candidate.")
    return None


def _extract_citation_urls(data: Any) -> list[tuple[str, str]]:
    """Walk a JSON-decoded response tree for citation-shaped {"url": ...} objects.

    Shape-agnostic on purpose: the exact tool-output schema for OpenAI's
    hosted web_search tool isn't pinned to one fixed nesting here, so this
    looks for any dict carrying a "url" string field anywhere in the tree.
    """

    found: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url.strip() and url not in seen_urls:
                title = node.get("title") or node.get("text") or ""
                found.append((url.strip(), str(title).strip()))
                seen_urls.add(url)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    return found


def _extract_urls_from_text(text: str) -> list[tuple[str, str]]:
    """Last-resort fallback: regex-scan output text for bare URLs."""

    urls: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(").,;")
        if url not in seen:
            urls.append((url, ""))
            seen.add(url)
    return urls
