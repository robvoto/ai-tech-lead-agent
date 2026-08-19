"""Bounded discovery of a target project's own guidance before Tech Lead Analysis.

Reuses the "project pack" shape (see project_pack_templates.py / agent_manifest.py's
``_PROJECT_PACK_REQUIRED_FILES``) — ``AGENTS.md``, ``docs/INDEX.md``, ``.skills/INDEX.md``
— and the same progressive-disclosure pattern this repo's own ``.skills/INDEX.md``
follows: read the small index/entry-point files first, follow at most two explicit
one-hop Markdown references from ``docs/INDEX.md``, and load one skill's full
detail only when it scores as relevant to the current request. This module never
scans the repository for alternatives and never hardcodes a project name, technology,
domain, document filename, or skill name — relevance is scored generically against
whatever entries each project's own indexes list, using the same request-term
scoring already used for local research-doc discovery (research_sources.py) and code
context (research_code_context.py).

Every file here is optional. A missing AGENTS.md/docs index/skills index is not an
error — it just means nothing gets added; AI Tech Lead's own core guidance remains
the safe default. This module does not decide when missing guidance is unsafe to
proceed without — that judgement stays with the existing risk-review and research
gates, which run independently of this step.

Bounds are kept small and local (not settings-owned) because they exist purely to
respect CONTEXT_MANAGEMENT.md's "~1,000 token" new-context-fragment budget, which is
a protocol-level constraint on prompt size rather than a product/admin tunable:
each added fragment is independently capped, and referenced documents are limited
to two one-hop files from the docs index rather than a directory or recursive scan.
"""

from __future__ import annotations

import re
from pathlib import Path

from .research_sources import _request_terms, _score_text, _truncate_text

_ENTRY_MAX_CHARS = 700
_SKILL_DETAIL_MAX_CHARS = 900
_REFERENCED_DOC_MAX_CHARS = 600
_MAX_REFERENCED_DOCS = 2
_DOC_REFERENCE_PATTERN = re.compile(
    r"`(?P<backtick>[^`]+\.md)`|\[[^\]]+\]\((?P<link>[^)#\s]+\.md)(?:#[^)]+)?\)"
)

# Two bullet shapes used by project skills indexes: this repo's own
# `` - `path` - summary `` convention, and a plain `- path.md: summary` convention.
# Kept local to this module rather than
# widening research_sources.py's shared `_DOC_INDEX_ENTRY_PATTERN`, which other
# code (local research-doc scoring) already depends on matching only the first
# shape. Both alternatives are generic line shapes, not tied to any project name.
_SKILL_INDEX_ENTRY_PATTERN = re.compile(
    r"^-\s+(?:`(?P<backtick_path>[^`]+)`\s*-\s*(?P<backtick_summary>.+)"
    r"|(?P<colon_path>\S+\.md)\s*:\s*(?P<colon_summary>.+))$"
)


def discover_project_guidance(request: str, project_root: str) -> tuple[str, ...]:
    """Return bounded notes from a target project's own authoritative entry points.

    Checks only ``AGENTS.md``, ``docs/INDEX.md``, and ``.skills/INDEX.md`` directly
    under ``project_root`` — the same three files ``agent_manifest.py``'s project-pack
    check already treats as the canonical minimal set. When ``.skills/INDEX.md``
    exists, also loads the single best-matching skill file (scored against its own
    index entry, not its full content — the whole point of progressive disclosure
    is not opening every skill just to score it). From ``docs/INDEX.md``, follows at
    most two explicit one-hop Markdown references under ``docs/`` and includes only
    bounded excerpts; it does not scan or recurse through the docs tree. These small
    limits are local protocol constants because they protect the prompt-context
    budget, rather than representing user- or administrator-owned behaviour.
    """

    if not project_root:
        return ()
    root = Path(project_root)
    if not root.is_dir():
        return ()

    request_terms = _request_terms(request)
    notes: list[str] = []

    agents_path = root / "AGENTS.md"
    if agents_path.is_file():
        text = _truncate_text(agents_path.read_text(encoding="utf-8"), _ENTRY_MAX_CHARS)
        notes.append(f"AGENTS.md: {text}")

    docs_index_path = root / "docs" / "INDEX.md"
    docs_index_text = ""
    if docs_index_path.is_file():
        docs_index_text = docs_index_path.read_text(encoding="utf-8")
        text = _truncate_text(docs_index_text, _ENTRY_MAX_CHARS)
        notes.append(f"docs/INDEX.md: {text}")

    skills_index_path = root / ".skills" / "INDEX.md"
    if skills_index_path.is_file():
        index_text = skills_index_path.read_text(encoding="utf-8")
        notes.append(f".skills/INDEX.md: {_truncate_text(index_text, _ENTRY_MAX_CHARS)}")
        best_skill = _best_matching_skill(index_text, skills_index_path, request_terms)
        if best_skill is not None:
            relative_path, summary, skill_text = best_skill
            notes.append(
                f".skills/{relative_path} ({summary}): "
                f"{_truncate_text(skill_text, _SKILL_DETAIL_MAX_CHARS)}"
            )

    if docs_index_text:
        for relative_path, document_text in _referenced_docs_from_index(
            root, docs_index_path, docs_index_text, request_terms
        ):
            notes.append(
                f"{relative_path}: "
                f"{_relevant_document_excerpt(document_text, request_terms)}"
            )

    return tuple(notes)


def _referenced_docs_from_index(
    project_root: Path,
    index_path: Path,
    index_text: str,
    request_terms: set[str],
) -> tuple[tuple[str, str], ...]:
    """Load at most two one-hop Markdown references explicitly named by the index.

    The index owns discovery. We never scan docs/ for alternatives, recurse through
    a referenced document, or follow a path outside docs/. Scoring only ranks
    already-authorised references for bounded context; it does not decide meaning.
    """

    docs_root = (project_root / "docs").resolve()
    candidates_by_path: dict[str, tuple[int, int, str, Path]] = {}
    order = 0
    for line in index_text.splitlines():
        for match in _DOC_REFERENCE_PATTERN.finditer(line):
            raw_path = (match.group("backtick") or match.group("link") or "").strip()
            if not raw_path or "://" in raw_path:
                continue
            document_path = (index_path.parent / raw_path).resolve()
            try:
                relative_to_docs = document_path.relative_to(docs_root)
                relative_to_project = document_path.relative_to(project_root).as_posix()
            except ValueError:
                continue
            if relative_to_docs.as_posix() == "INDEX.md":
                continue
            if document_path.suffix.lower() != ".md" or not document_path.is_file():
                continue
            score = _score_text(request_terms, f"{raw_path} {line}")
            existing = candidates_by_path.get(relative_to_project)
            if existing is None:
                candidates_by_path[relative_to_project] = (
                    score,
                    order,
                    relative_to_project,
                    document_path,
                )
                order += 1
            elif score > existing[0]:
                candidates_by_path[relative_to_project] = (
                    score,
                    existing[1],
                    relative_to_project,
                    document_path,
                )

    candidates = list(candidates_by_path.values())
    if not candidates:
        return ()

    ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))
    chosen: list[tuple[int, int, str, Path]] = []
    for candidate in ranked:
        if candidate[0] <= 0:
            continue
        chosen.append(candidate)
        if len(chosen) >= _MAX_REFERENCED_DOCS:
            break
    if len(chosen) < _MAX_REFERENCED_DOCS:
        chosen_paths = {item[2] for item in chosen}
        for candidate in sorted(candidates, key=lambda item: item[1]):
            if candidate[2] in chosen_paths:
                continue
            chosen.append(candidate)
            chosen_paths.add(candidate[2])
            if len(chosen) >= _MAX_REFERENCED_DOCS:
                break

    return tuple(
        (relative_path, document_path.read_text(encoding="utf-8"))
        for _, _, relative_path, document_path in chosen
    )


def _relevant_document_excerpt(text: str, request_terms: set[str]) -> str:
    """Return one bounded nearby-line window that best matches the request."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    best_score = 0
    best_start = 0
    for start in range(len(lines)):
        window = " ".join(lines[start : start + 5])
        score = _score_text(request_terms, window)
        if score > best_score:
            best_score = score
            best_start = start
    excerpt_start = max(0, best_start - 1) if best_score else 0
    excerpt = " ".join(lines[excerpt_start : best_start + 5])
    return _truncate_text(excerpt, _REFERENCED_DOC_MAX_CHARS)


def _best_matching_skill(
    index_text: str, index_path: Path, request_terms: set[str]
) -> tuple[str, str, str] | None:
    """Pick the single skill whose index entry (path + one-line summary) scores
    highest against the request — never the skill file's own content, so scoring
    every candidate never requires opening more than the index itself."""

    best_score = 0
    best: tuple[str, str, str] | None = None
    for line in index_text.splitlines():
        match = _SKILL_INDEX_ENTRY_PATTERN.match(line.strip())
        if not match:
            continue
        groups = match.groupdict()
        relative_path = (groups["backtick_path"] or groups["colon_path"]).strip()
        summary = (groups["backtick_summary"] or groups["colon_summary"]).strip()
        skill_path = (index_path.parent / relative_path).resolve()
        if not skill_path.is_file():
            continue
        score = _score_text(request_terms, f"{relative_path} {summary}")
        if score > best_score:
            best_score = score
            best = (relative_path, summary, skill_path.read_text(encoding="utf-8"))
    return best
