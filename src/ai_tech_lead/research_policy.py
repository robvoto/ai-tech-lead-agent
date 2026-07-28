"""Bounded, technology-aware research source policy.

The policy selects a small set of trusted official documentation domains and
seed pages from the task plus bounded target-repository signals. It never
performs crawling and never changes the approval or URL-safety gates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .app_settings import AppSettings

_MAX_SIGNAL_FILE_BYTES = 64_000
_SIGNAL_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "README.md",
    "README.rst",
)


@dataclass(frozen=True)
class ResearchSourceProfile:
    name: str
    indicators: tuple[str, ...]
    domains: tuple[str, ...]
    seed_urls: tuple[str, ...]


@dataclass(frozen=True)
class ResearchPolicyDecision:
    profile_names: tuple[str, ...]
    trusted_domains: tuple[str, ...]
    seed_urls: tuple[str, ...]


_PROFILES = (
    ResearchSourceProfile(
        name="langgraph-langchain",
        indicators=("langgraph", "langchain", "stategraph", "langsmith"),
        domains=("docs.langchain.com",),
        seed_urls=(
            "https://docs.langchain.com/oss/python/langgraph/overview",
            "https://docs.langchain.com/oss/python/langgraph/interrupts",
            "https://docs.langchain.com/oss/python/langgraph/checkpointers",
            "https://docs.langchain.com/oss/python/langgraph/application-structure",
        ),
    ),
    ResearchSourceProfile(
        name="litellm",
        indicators=("litellm", "litellm_proxy", "litellm router"),
        domains=("docs.litellm.ai",),
        seed_urls=("https://docs.litellm.ai/docs/",),
    ),
    ResearchSourceProfile(
        name="telegram",
        indicators=("telegram", "bot api", "core.telegram"),
        domains=("core.telegram.org",),
        seed_urls=("https://core.telegram.org/bots/api",),
    ),
    ResearchSourceProfile(
        name="python",
        indicators=("python", "pyproject.toml", "pytest", "fastapi", "pydantic"),
        domains=("docs.python.org",),
        seed_urls=("https://docs.python.org/3/",),
    ),
)


def select_research_policy(
    request: str,
    settings: AppSettings,
    *,
    project_root: str | Path | None = None,
) -> ResearchPolicyDecision:
    """Select relevant official-source profiles using bounded local signals."""

    request_text = request.lower()
    repository_text = _read_repository_signals(project_root or settings.project_root)
    request_profiles = [profile for profile in _PROFILES if _matches(profile, request_text)]
    repository_profiles = [profile for profile in _PROFILES if _matches(profile, repository_text)]
    # A technology named in the knowledge-gap question is authoritative. Repository
    # signals are only a bounded fallback for generic questions.
    selected = request_profiles or repository_profiles

    if selected:
        domains = _dedupe(domain.lower() for profile in selected for domain in profile.domains)
    else:
        domains = _dedupe(domain.lower() for domain in settings.research_allowed_domains)

    seeds = _dedupe(url for profile in selected for url in profile.seed_urls)
    configured = [
        url
        for url in settings.research_online_source_urls
        if is_url_on_trusted_domain(url, domains)
    ]
    seeds = _dedupe([*seeds, *configured])

    return ResearchPolicyDecision(
        profile_names=tuple(profile.name for profile in selected),
        trusted_domains=tuple(domains),
        seed_urls=tuple(seeds[: settings.research_max_online_source_urls]),
    )


def is_url_on_trusted_domain(url: str, trusted_domains: tuple[str, ...] | list[str]) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in trusted_domains)


def _matches(profile: ResearchSourceProfile, text: str) -> bool:
    return any(_indicator_present(indicator, text) for indicator in profile.indicators)


def _indicator_present(indicator: str, text: str) -> bool:
    if any(char in indicator for char in (".", " ", "_")):
        return indicator in text
    return re.search(rf"\b{re.escape(indicator)}\b", text) is not None


def _read_repository_signals(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    snippets: list[str] = []
    for relative in _SIGNAL_FILES:
        path = root / relative
        if not path.is_file():
            continue
        try:
            snippets.append(relative.lower())
            content = path.read_text(encoding="utf-8", errors="ignore")
            snippets.append(content[:_MAX_SIGNAL_FILE_BYTES].lower())
        except OSError:
            continue
    return "\n".join(snippets)


def _dedupe(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
