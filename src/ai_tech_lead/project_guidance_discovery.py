"""Bounded discovery of a target project's own guidance before Tech Lead Analysis.

Reuses the "project pack" shape (see project_pack_templates.py / agent_manifest.py's
``_PROJECT_PACK_REQUIRED_FILES``) — ``AGENTS.md``, ``docs/INDEX.md``, ``.skills/INDEX.md``
— and the same progressive-disclosure pattern this repo's own ``.skills/INDEX.md``
follows: read the small index/entry-point files first, and load one skill's full
detail only when it scores as relevant to the current request. This module never
scans the repository beyond these few fixed, well-known relative paths, and never
hardcodes a project name, technology, domain, or skill name — relevance is scored
generically against whatever entries each project's own index happens to list,
using the same request-term scoring already used for local research-doc discovery
(research_sources.py) and code context (research_code_context.py).

Every file here is optional. A missing AGENTS.md/docs index/skills index is not an
error — it just means nothing gets added; AI Tech Lead's own core guidance remains
the safe default. This module does not decide when missing guidance is unsafe to
proceed without — that judgement stays with the existing risk-review and research
gates, which run independently of this step.

Bounds are kept small and local (not settings-owned) because they exist purely to
respect CONTEXT_MANAGEMENT.md's "~1,000 token" new-context-fragment budget, which is
a protocol-level constraint on prompt size rather than a product/admin tunable: the
worst case (AGENTS.md + docs index + skills index + one drilled-in skill) is well
under 3,000 characters (~750 tokens).
"""

from __future__ import annotations

import re
from pathlib import Path

from .research_sources import _request_terms, _score_text, _truncate_text

_ENTRY_MAX_CHARS = 700
_SKILL_DETAIL_MAX_CHARS = 900

# Two bullet shapes observed across real project skills indexes: this repo's own
# `` - `path` - summary `` convention, and a plain `- path.md: summary` convention
# (e.g. Agent Factory's `.skills/INDEX.md`). Kept local to this module rather than
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
    is not opening every skill just to score it).
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
    if docs_index_path.is_file():
        text = _truncate_text(docs_index_path.read_text(encoding="utf-8"), _ENTRY_MAX_CHARS)
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

    return tuple(notes)


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
