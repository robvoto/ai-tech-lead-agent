"""Bounded code-context lookup for the knowledge-gap check.

Scores files under the configured watched directories by filename/path
relevance to the request, then reads and truncates the top few matches.
This is narrower than research_sources.py's doc-index lookup: an empty
result here means "no relevant code found," not "here's some noise" —
there is no fallback-to-everything, since this evidence exists purely to
suppress false-positive knowledge gaps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .research_sources import (
    _relative_path_text,
    _request_terms,
    _resolve_path,
    _score_text,
    _truncate_text,
)

logger = logging.getLogger(LOGGER_NAME)

_IGNORED_PATH_PARTS = {"__pycache__", "node_modules", ".venv", "venv", ".git"}


@dataclass(frozen=True)
class CodeContextSource:
    """One bounded code excerpt selected as relevant to the request."""

    path: str
    excerpt: str


def collect_code_context(
    request: str,
    settings: AppSettings,
    *,
    project_root_override: str | None = None,
) -> list[CodeContextSource]:
    """Collect a small set of relevant code excerpts from the watched directories.

    `project_root_override` scopes the scan to a resolved target project
    instead of `settings.project_root` (this repo) — used when the task
    being worked on is about a different project than AI Tech Lead itself.
    Watched-directory names are still AI-Tech-Lead-scoped configuration, so
    a target project without matching directory names simply yields no
    context rather than an error.
    """

    if not settings.research_code_context_enabled:
        return []

    request_terms = _request_terms(request)
    root_text = project_root_override or settings.project_root
    project_root = Path(root_text)
    candidates: list[tuple[int, Path]] = []

    for directory_text in settings.watched_directories:
        directory = _resolve_path(directory_text, root_text)
        if not directory.exists():
            logger.info("[LEARN] Watched directory missing: %s", directory)
            continue

        for file_path in directory.rglob("*"):
            if not file_path.is_file() or _is_ignored(file_path):
                continue
            relative_text = _relative_path_text(file_path, project_root)
            score = _score_text(request_terms, relative_text)
            if score > 0:
                candidates.append((score, file_path))

    candidates.sort(key=lambda item: (-item[0], str(item[1])))
    selected_paths = [path for _, path in candidates[: settings.research_max_code_context_files]]

    sources: list[CodeContextSource] = []
    for file_path in selected_paths:
        try:
            raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            logger.warning("[LEARN] Unable to read code context file %s: %s", file_path, error)
            continue
        excerpt = _truncate_text(raw_text, settings.research_max_excerpt_chars)
        sources.append(
            CodeContextSource(
                path=_relative_path_text(file_path, project_root),
                excerpt=excerpt,
            )
        )

    if sources:
        logger.info(
            "[LEARN] Code context selected: %d file(s): %s",
            len(sources),
            ", ".join(source.path for source in sources),
        )
    else:
        logger.info("[LEARN] Code context selected: no files matched")

    return sources


def format_code_context_for_prompt(
    sources: list[CodeContextSource],
    *,
    limit: int = 3000,
) -> str:
    """Render bounded code context for the knowledge-gap prompt."""

    if not sources:
        return "(none)"

    rendered = [f"- {source.path}: {source.excerpt}" for source in sources]
    text = "\n".join(rendered)
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _is_ignored(path: Path) -> bool:
    return any(part.startswith(".") or part in _IGNORED_PATH_PARTS for part in path.parts)
