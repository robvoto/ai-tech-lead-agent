"""Lightweight cache for implementation-pattern research notes.

The refinement flow reads the cache index before it considers any online lookup.
This stays intentionally small and file-backed instead of becoming a full RAG
system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

RESEARCH_INDEX_PATH = PROJECT_ROOT / "docs" / "research" / "INDEX.md"
RESEARCH_NOTE_STALE_AFTER_DAYS = 90

_INDEX_ENTRY_PATTERN = re.compile(
    r"^-\s+\[(?P<filename>[^\]]+)\]\((?P<link>[^\)]+)\)\s+—\s+(?P<summary>.+)$"
)


@dataclass(frozen=True)
class ResearchCacheEntry:
    """One cached research note referenced from docs/research/INDEX.md."""

    path: Path
    title: str
    summary: str
    body: str
    sources: list[str]
    refreshed_on: date | None
    freshness_risk: str


def load_research_cache_entries(
    index_path: Path | None = None,
    *,
    today: date | None = None,
) -> list[ResearchCacheEntry]:
    """Load all research notes listed in the cache index.

    The cache is small enough that the refinement flow can include every note in
    prompt context without turning into a retrieval system.
    """

    resolved_index_path = index_path or RESEARCH_INDEX_PATH
    if not resolved_index_path.exists():
        raise FileNotFoundError(f"Research index not found: {resolved_index_path}")

    current_day = today or date.today()
    entries: list[ResearchCacheEntry] = []

    for line in resolved_index_path.read_text(encoding="utf-8").splitlines():
        match = _INDEX_ENTRY_PATTERN.match(line.strip())
        if not match:
            continue

        note_path = (resolved_index_path.parent / match.group("link")).resolve()
        if not note_path.exists():
            raise FileNotFoundError(f"Research note not found: {note_path}")

        note_data = _parse_note(note_path)
        refreshed_on = _parse_date(note_data.get("date"))
        freshness_risk = _freshness_risk(refreshed_on, current_day)
        entries.append(
            ResearchCacheEntry(
                path=note_path,
                title=str(
                    note_data.get("topic") or note_data.get("title") or match.group("filename")
                ),
                summary=match.group("summary").strip(),
                body=_extract_summary_body(note_path.read_text(encoding="utf-8")),
                sources=_parse_sources(note_data.get("sources")),
                refreshed_on=refreshed_on,
                freshness_risk=freshness_risk,
            )
        )

    return entries


def format_research_cache_for_prompt(
    entries: list[ResearchCacheEntry],
    *,
    limit: int = 4000,
) -> str:
    """Render cache entries as bounded prompt context."""

    if not entries:
        return "No cached research notes were available."

    blocks: list[str] = ["Cached research notes:"]
    for entry in entries:
        blocks.append(f"- {entry.path.name}")
        blocks.append(f"  Title: {entry.title}")
        blocks.append(f"  Summary: {entry.summary}")
        blocks.append(f"  Freshness risk: {entry.freshness_risk}")
        if entry.sources:
            blocks.append("  Sources:")
            blocks.extend(f"  - {source}" for source in entry.sources)
        if entry.body:
            blocks.append("  Note excerpt:")
            blocks.append(_indent_text(_truncate_text(entry.body, 800), prefix="  "))

    return _truncate_text("\n".join(blocks), limit)


def _parse_note(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    frontmatter: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in lines[1:]:
        stripped = line.rstrip()
        if stripped.strip() == "---":
            break
        if stripped.startswith("  - ") and current_list is not None:
            current_list.append(stripped[4:].strip())
            continue
        if stripped.startswith("- ") and current_list is not None:
            current_list.append(stripped[2:].strip())
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            current_key = key.strip()
            value_text = value.strip()
            if value_text:
                frontmatter[current_key] = value_text
                current_list = None
            else:
                current_list = []
                frontmatter[current_key] = current_list
            continue
        if current_key and current_list is not None and stripped.startswith("  "):
            current_list.append(stripped.strip())

    return frontmatter


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
    except ValueError:
        return None
    return date(year, month, day)


def _parse_sources(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extract_summary_body(text: str) -> str:
    lines = text.splitlines()
    body_lines: list[str] = []
    in_frontmatter = False
    in_summary = False

    for line in lines:
        stripped = line.strip()
        if line.startswith("---"):
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if stripped.startswith("## "):
            in_summary = stripped.lower() == "## summary"
            continue
        if in_summary:
            body_lines.append(line)

    if body_lines:
        return "\n".join(body_lines).strip()

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped

    return ""


def _freshness_risk(refreshed_on: date | None, today: date) -> str:
    if refreshed_on is None:
        return (
            "Medium: the note has no refresh date, so re-check if APIs or best practices changed."
        )

    age_days = (today - refreshed_on).days
    if age_days < 0:
        return "Low: the note is dated in the future relative to the current clock."
    if age_days <= 30:
        return f"Low: refreshed {age_days} days ago."
    if age_days <= RESEARCH_NOTE_STALE_AFTER_DAYS:
        return f"Moderate: refreshed {age_days} days ago."
    return f"High: refreshed {age_days} days ago and should be rechecked."


def _indent_text(text: str, *, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."
