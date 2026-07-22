"""Bounded research source lookup for local docs and approved online docs.

This module keeps research discovery explicit:
- first look through configured local documentation indexes
- then fetch a small approved list of online documentation URLs

It does not perform open-ended web search or unbounded crawling.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .research_cache import (
    ResearchCacheEntry,
    load_research_cache_entries,
    write_research_cache_note,
)

logger = logging.getLogger(LOGGER_NAME)

_DOC_INDEX_ENTRY_PATTERN = re.compile(r"^-\s+`(?P<path>[^`]+)`\s*-\s*(?P<summary>.+)$")


@dataclass(frozen=True)
class ResearchSource:
    """One bounded research source for handoff and logging."""

    title: str
    location: str
    summary: str
    excerpt: str
    source_kind: str

    def to_note(self) -> str:
        """Render a short raw note that stays suitable for prompt assembly."""

        note = f"{self.source_kind}: {self.title} | {self.location}"
        if self.summary:
            note = f"{note} | {self.summary}"
        if self.excerpt:
            note = f"{note} | {self.excerpt}"
        return note


def collect_local_research_sources(
    request: str,
    settings: AppSettings,
) -> list[ResearchSource]:
    """Collect relevant sources from the configured local documentation indexes."""

    request_terms = _request_terms(request)
    _log_research_request("Local research selection", request, request_terms)
    candidates: list[tuple[int, ResearchSource]] = []

    for index_path_text in settings.research_local_index_paths:
        index_path = _resolve_path(index_path_text, settings.project_root)
        if not index_path.exists():
            logger.info("[LEARN] Local research index missing: %s", index_path)
            continue

        if _is_research_index(index_path):
            try:
                research_entries = load_research_cache_entries(index_path=index_path)
            except FileNotFoundError as error:
                logger.warning("[LEARN] Local research cache unavailable: %s", error)
                continue

            candidates.extend(
                (score, source)
                for score, source in _score_research_cache_entries(
                    research_entries,
                    request_terms=request_terms,
                    project_root=Path(settings.project_root),
                    max_excerpt_chars=settings.research_max_excerpt_chars,
                    settings=settings,
                )
                if score > 0
            )
            continue

        candidates.extend(
            (score, source)
            for score, source in _score_docs_index_entries(
                index_path=index_path,
                request_terms=request_terms,
                project_root=Path(settings.project_root),
                max_excerpt_chars=settings.research_max_excerpt_chars,
            )
            if score > 0
        )

    candidates.sort(key=lambda item: (-item[0], item[1].title.lower(), item[1].location))
    selected = [source for _, source in candidates[: settings.research_max_local_sources]]
    _log_selected_sources("Local research selection", selected)
    if selected:
        logger.info(
            "[LEARN] Local research evidence preview:\n%s",
            format_research_sources_for_prompt(selected, limit=1200),
        )
    return selected


def collect_online_research_sources(
    request: str,
    settings: AppSettings,
) -> list[ResearchSource]:
    """Fetch a bounded set of approved online documentation pages in parallel."""

    request_terms = _request_terms(request)
    _log_research_request("Online research selection", request, request_terms)
    selected_urls = settings.research_online_source_urls[: settings.research_max_online_source_urls]
    logger.debug(
        "[LEARN] Online research candidate URLs (%d): %s",
        len(selected_urls),
        ", ".join(selected_urls) if selected_urls else "<none>",
    )
    sources: list[tuple[int, ResearchSource]] = []

    def _fetch(url: str) -> tuple[str, ResearchSource | None]:
        try:
            return url, _fetch_online_source(
                url,
                timeout_seconds=settings.research_fetch_timeout_seconds,
                max_excerpt_chars=settings.research_max_excerpt_chars,
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            logger.warning("[LEARN] Online research source failed: %s (%s)", url, error)
            return url, None

    with ThreadPoolExecutor(max_workers=max(1, len(selected_urls))) as executor:
        fetch_results = list(executor.map(_fetch, selected_urls))

    for _url, source in fetch_results:
        if source is None:
            continue
        score = _score_text(request_terms, " ".join([source.title, source.summary, source.excerpt]))
        _log_scored_candidate(
            label="Online research candidate",
            score=score,
            source=source,
            matched_terms=_matched_request_terms(
                request_terms, " ".join([source.title, source.summary, source.excerpt])
            ),
        )
        sources.append((score, source))

    sources.sort(key=lambda item: (-item[0], item[1].title.lower(), item[1].location))
    selected = [source for score, source in sources if score > 0]
    if not selected:
        logger.debug(
            "[LEARN] Online research selection fallback: no positive-scoring sources matched "
            "request terms; returning all %d fetched source(s).",
            len(sources),
        )
        selected = [source for _, source in sources]

    _log_selected_sources("Online research selection", selected)
    if selected:
        logger.info(
            "[LEARN] Online research evidence preview:\n%s",
            format_research_sources_for_prompt(selected, limit=1200),
        )
    return selected


def format_research_sources_for_prompt(
    sources: Iterable[ResearchSource], *, limit: int = 3000
) -> str:
    """Render bounded research evidence for a prompt or handoff."""

    rendered: list[str] = []
    for source in sources:
        rendered.append(f"- {source.to_note()}")

    if not rendered:
        return "No research evidence collected."

    text = "\n".join(rendered)
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _log_selected_sources(label: str, sources: list[ResearchSource]) -> None:
    if not sources:
        logger.info("[LEARN] %s: no sources selected", label)
        return

    source_kind_counts: dict[str, int] = {}
    for source in sources:
        source_kind_counts[source.source_kind] = source_kind_counts.get(source.source_kind, 0) + 1

    kind_summary = ", ".join(
        f"{count} {kind}" for kind, count in sorted(source_kind_counts.items())
    )
    preview_sources = ", ".join(f"{source.source_kind}:{source.title}" for source in sources[:4])
    if len(sources) > 4:
        preview_sources = f"{preview_sources}, ... (+{len(sources) - 4} more)"

    logger.info(
        "[LEARN] %s: selected %d source(s) (%s): %s",
        label,
        len(sources),
        kind_summary,
        preview_sources,
    )


def _log_research_request(label: str, request: str, request_terms: set[str]) -> None:
    logger.debug("[LEARN] %s request: %s", label, " ".join(request.split()))
    if request_terms:
        logger.debug(
            "[LEARN] %s request terms (%d): %s",
            label,
            len(request_terms),
            ", ".join(sorted(request_terms)),
        )
        return
    logger.debug(
        "[LEARN] %s request terms: none extracted from 4+ character tokens; "
        "relevance scoring may fall back to broad selection.",
        label,
    )


def _log_scored_candidate(
    *,
    label: str,
    score: int,
    source: ResearchSource,
    matched_terms: list[str],
) -> None:
    logger.debug(
        "[LEARN] %s: score=%d matched_terms=%s source=%s:%s location=%s",
        label,
        score,
        ",".join(matched_terms) if matched_terms else "<none>",
        source.source_kind,
        source.title,
        source.location,
    )


def _score_docs_index_entries(
    *,
    index_path: Path,
    request_terms: set[str],
    project_root: Path,
    max_excerpt_chars: int,
) -> list[tuple[int, ResearchSource]]:
    candidates: list[tuple[int, ResearchSource]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = _DOC_INDEX_ENTRY_PATTERN.match(line.strip())
        if not match:
            continue

        relative_path = match.group("path").strip()
        summary = match.group("summary").strip()
        doc_path = (project_root / "docs" / relative_path).resolve()
        if not doc_path.exists():
            logger.info("[LEARN] Local docs source missing: %s", doc_path)
            continue

        title = Path(relative_path).name
        excerpt = _extract_local_excerpt(doc_path, max_excerpt_chars=max_excerpt_chars)
        searchable_text = " ".join([title, summary, excerpt, relative_path])
        score = _score_text(request_terms, searchable_text)
        source = ResearchSource(
            title=title,
            location=str(doc_path.relative_to(project_root)),
            summary=summary,
            excerpt=excerpt,
            source_kind="local-doc",
        )
        _log_scored_candidate(
            label="Local docs candidate",
            score=score,
            source=source,
            matched_terms=_matched_request_terms(request_terms, searchable_text),
        )
        candidates.append(
            (
                score,
                source,
            )
        )
    return candidates


def _score_research_cache_entries(
    entries: list[ResearchCacheEntry],
    *,
    request_terms: set[str],
    project_root: Path,
    max_excerpt_chars: int,
    settings: AppSettings,
) -> list[tuple[int, ResearchSource]]:
    candidates: list[tuple[int, ResearchSource]] = []
    for entry in entries:
        refreshed_entry = _refresh_stale_research_cache_entry(entry, settings=settings)
        summary = refreshed_entry.summary.strip()
        excerpt = _truncate_text(refreshed_entry.body.strip(), max_excerpt_chars)
        relative_location = _relative_path_text(entry.path, project_root)
        searchable_text = " ".join([refreshed_entry.title, summary, excerpt, relative_location])
        score = _score_text(request_terms, searchable_text)
        source = ResearchSource(
            title=refreshed_entry.title,
            location=relative_location,
            summary=summary,
            excerpt=excerpt,
            source_kind="local-research",
        )
        _log_scored_candidate(
            label="Local research cache candidate",
            score=score,
            source=source,
            matched_terms=_matched_request_terms(request_terms, searchable_text),
        )
        candidates.append(
            (
                score,
                source,
            )
        )
    return candidates


def _refresh_stale_research_cache_entry(
    entry: ResearchCacheEntry,
    *,
    settings: AppSettings,
) -> ResearchCacheEntry:
    if not entry.freshness_risk.startswith("High"):
        return entry
    if len(entry.sources) != 1:
        return entry

    source_url = entry.sources[0]
    if source_url not in settings.research_online_source_urls:
        return entry

    try:
        refreshed_source = _fetch_online_source(
            source_url,
            timeout_seconds=settings.research_fetch_timeout_seconds,
            max_excerpt_chars=settings.research_max_excerpt_chars,
        )
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        logger.warning("[LEARN] Stale research cache refresh failed: %s (%s)", source_url, error)
        return entry

    summary = refreshed_source.summary.strip() or refreshed_source.excerpt.strip() or entry.summary
    excerpt = refreshed_source.excerpt.strip() or summary
    body_text = summary or excerpt or entry.body
    try:
        write_research_cache_note(
            entry.path,
            title=refreshed_source.title or entry.title,
            location=source_url,
            summary=summary,
            excerpt=excerpt,
            today=date.today(),
        )
    except OSError as error:
        logger.warning(
            "[LEARN] Unable to update stale research cache note %s: %s",
            entry.path,
            error,
        )
        return entry

    refreshed_entry = replace(
        entry,
        title=refreshed_source.title or entry.title,
        summary=summary,
        body=body_text,
        sources=[source_url],
        refreshed_on=date.today(),
        freshness_risk="Low: refreshed 0 days ago.",
    )
    logger.info("[LEARN] Refreshed stale research cache entry: %s", entry.path.name)
    return refreshed_entry


def _fetch_online_source(
    url: str,
    *,
    timeout_seconds: int,
    max_excerpt_chars: int,
) -> ResearchSource:
    request = Request(
        url,
        headers={
            "User-Agent": "ai-tech-lead-research/1.0",
            "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        raw_body = response.read().decode(charset, errors="replace")

    title, summary, excerpt = _extract_online_document_fields(
        raw_body,
        content_type=content_type,
        max_excerpt_chars=max_excerpt_chars,
    )
    return ResearchSource(
        title=title or url,
        location=url,
        summary=summary,
        excerpt=excerpt,
        source_kind="online-doc",
    )


class _HtmlFieldExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.summary_parts: list[str] = []
        self._capture_title = False
        self._skip_depth = 0
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._capture_title = True
            return
        if tag in {"header", "nav", "footer", "aside", "script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag in {"body", "main", "article"}:
            self._in_body = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._capture_title = False
            return
        if (
            tag in {"header", "nav", "footer", "aside", "script", "style", "noscript"}
            and self._skip_depth
        ):
            self._skip_depth -= 1
            return
        if tag == "body":
            self._in_body = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or self._skip_depth:
            return
        if self._capture_title:
            self.title_parts.append(text)
            return
        if self._in_body:
            self.summary_parts.append(text)


def _extract_online_document_fields(
    body: str,
    *,
    content_type: str,
    max_excerpt_chars: int,
) -> tuple[str, str, str]:
    if content_type in {"text/plain", "text/markdown"} or _looks_like_markdown(body):
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        title = lines[0].lstrip("# ").strip() if lines else ""
        excerpt = _truncate_text("\n".join(lines[1:]), max_excerpt_chars)
        summary = lines[1] if len(lines) > 1 else ""
        return title, summary, excerpt

    extractor = _HtmlFieldExtractor()
    extractor.feed(body)
    title = " ".join(extractor.title_parts).strip()
    summary = " ".join(extractor.summary_parts).strip()
    excerpt = _truncate_text(summary, max_excerpt_chars)
    if not summary:
        excerpt = _truncate_text(_strip_tags(body), max_excerpt_chars)
    return title, summary, excerpt


def _extract_local_excerpt(path: Path, *, max_excerpt_chars: int) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    excerpt_lines: list[str] = []
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if line.startswith("---"):
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if stripped.startswith("## "):
            if excerpt_lines:
                break
            continue
        if stripped:
            excerpt_lines.append(stripped)
        if len("\n".join(excerpt_lines)) >= max_excerpt_chars:
            break
    excerpt = " ".join(excerpt_lines)
    if not excerpt:
        excerpt = _truncate_text(text, max_excerpt_chars)
    return _truncate_text(excerpt, max_excerpt_chars)


def _request_terms(request: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", request.lower()) if len(word) >= 4}


def _score_text(request_terms: set[str], text: str) -> int:
    normalized = text.lower()
    return sum(1 for term in request_terms if term in normalized)


def _matched_request_terms(request_terms: set[str], text: str) -> list[str]:
    normalized = text.lower()
    return [term for term in sorted(request_terms) if term in normalized]


def _resolve_path(path_text: str, project_root: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (Path(project_root) / path).resolve()


def _is_research_index(index_path: Path) -> bool:
    return index_path.as_posix().endswith("/docs/research/INDEX.md")


def _relative_path_text(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _looks_like_markdown(body: str) -> bool:
    stripped = body.lstrip()
    return bool(
        stripped.startswith(("# ", "## ", "### ", "- ", "* ", "> ", "```"))
        or re.search(r"^#{1,6}\s+", body, flags=re.MULTILINE)
    )


def _truncate_text(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 3:
        return cleaned[:limit]
    return cleaned[: limit - 3].rstrip() + "..."
