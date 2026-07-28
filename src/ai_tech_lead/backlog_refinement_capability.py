"""Shared backlog refinement capability for Hub and Telegram."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .app_settings import AppSettings
from .backlog_draft_builder import (
    BacklogRefinementBuildError,
    BacklogRefinementBuildResult,
    build_backlog_refinement_from_text,
)
from .backlog_repository import (
    BacklogItem,
    BacklogRefinementDraft,
    BacklogRepositoryProtocol,
    render_backlog_refinement_draft,
)
from .backlog_status import BacklogStatus
from .config import PROJECT_ROOT

_BACKLOG_AUTHORING_SKILL_PATH = PROJECT_ROOT / ".skills" / "backlog-item-authoring" / "SKILL.md"
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_BLOCKING_MATCH_KINDS = {
    "likely_duplicate",
    "already_done_equivalent",
    "obsolete_equivalent",
    "conflicting_scope",
}


@dataclass(frozen=True)
class BacklogRefinementMatch:
    item_id: str
    title: str
    status: str
    kind: str
    reason: str

    def to_payload(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "status": self.status,
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BacklogRefinementProposal:
    draft: BacklogRefinementDraft
    source: str
    skill_path: str
    matches: tuple[BacklogRefinementMatch, ...]
    blocked: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "draft": _draft_to_payload(self.draft),
            "source": self.source,
            "skill_path": self.skill_path,
            "matches": [match.to_payload() for match in self.matches],
            "blocked": self.blocked,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> BacklogRefinementProposal:
        raw_draft = payload.get("draft")
        if not isinstance(raw_draft, dict):
            raise ValueError("backlog refinement proposal draft is required")
        raw_matches = payload.get("matches") or []
        if not isinstance(raw_matches, list):
            raise ValueError("backlog refinement proposal matches must be a list")
        return cls(
            draft=_draft_from_payload(raw_draft),
            source=str(payload.get("source", "")).strip(),
            skill_path=str(payload.get("skill_path", "")).strip(),
            matches=tuple(
                BacklogRefinementMatch(
                    item_id=str(item.get("item_id", "")).strip(),
                    title=str(item.get("title", "")).strip(),
                    status=str(item.get("status", "")).strip(),
                    kind=str(item.get("kind", "")).strip(),
                    reason=str(item.get("reason", "")).strip(),
                )
                for item in raw_matches
                if isinstance(item, dict)
            ),
            blocked=bool(payload.get("blocked", False)),
        )


def prepare_backlog_refinement_proposal(
    *,
    text: str,
    repository: BacklogRepositoryProtocol,
    settings: AppSettings,
    item_id_prefix: str,
) -> BacklogRefinementProposal:
    """Build one refined backlog draft and analyze full-backlog conflicts."""

    skill_text = load_backlog_item_authoring_skill()
    build_result = build_backlog_refinement_from_text(
        text=text,
        repository=repository,
        settings=settings,
        item_id_prefix=item_id_prefix,
        skill_instructions=skill_text,
    )
    matches = analyze_backlog_refinement_matches(
        draft=build_result.draft,
        repository=repository,
    )
    return BacklogRefinementProposal(
        draft=build_result.draft,
        source=build_result.source,
        skill_path=str(_BACKLOG_AUTHORING_SKILL_PATH),
        matches=matches,
        blocked=any(match.kind in _BLOCKING_MATCH_KINDS for match in matches),
    )


def load_backlog_item_authoring_skill() -> str:
    """Load the runtime backlog authoring skill text from disk."""

    if not _BACKLOG_AUTHORING_SKILL_PATH.is_file():
        raise FileNotFoundError(
            f"Backlog authoring skill file not found: {_BACKLOG_AUTHORING_SKILL_PATH}"
        )
    return _BACKLOG_AUTHORING_SKILL_PATH.read_text(encoding="utf-8").strip()


def infer_backlog_item_prefix(repository: BacklogRepositoryProtocol) -> str:
    """Infer one project backlog ID prefix from the existing repository contents."""

    prefixes = sorted(
        {
            match.group(1)
            for item in repository.list_items()
            if (match := re.fullmatch(r"([A-Z]+)-\d{3}", item.item_id.strip().upper()))
        }
    )
    if len(prefixes) == 1:
        return prefixes[0]
    if not prefixes:
        raise ValueError(
            "Could not infer a backlog item prefix from the repository. "
            "Supply target-project backlog item_id_prefix explicitly."
        )
    raise ValueError(
        "Could not infer one backlog item prefix from the repository because multiple "
        f"prefixes are present: {', '.join(prefixes)}. Supply item_id_prefix explicitly."
    )


def analyze_backlog_refinement_matches(
    *,
    draft: BacklogRefinementDraft,
    repository: BacklogRepositoryProtocol,
) -> tuple[BacklogRefinementMatch, ...]:
    """Check the full backlog for likely duplicates and scope conflicts."""

    matches: list[BacklogRefinementMatch] = []
    draft_title_tokens = _significant_tokens(draft.title)
    draft_scope_tokens = _significant_tokens(" ".join(draft.scope))
    draft_outcome_tokens = _significant_tokens(draft.desired_outcome)
    draft_out_of_scope_tokens = _significant_tokens(" ".join(draft.out_of_scope))

    for item in repository.list_items():
        if item.item_id.strip().upper() == draft.item_id.strip().upper():
            continue
        title_similarity = _token_overlap_ratio(draft_title_tokens, _significant_tokens(item.title))
        body_tokens = _significant_tokens(item.body)
        scope_overlap = _token_overlap_ratio(draft_scope_tokens, body_tokens)
        outcome_overlap = _token_overlap_ratio(draft_outcome_tokens, body_tokens)
        out_of_scope_overlap = _token_overlap_ratio(draft_out_of_scope_tokens, body_tokens)

        kind = ""
        reason = ""
        if _normalize_text(draft.title) == _normalize_text(item.title) or title_similarity >= 0.8:
            if item.status == BacklogStatus.DONE:
                kind = "already_done_equivalent"
                reason = "Very similar title to a completed backlog item."
            elif item.status in {BacklogStatus.OBSOLETE, BacklogStatus.WONT_DO, BacklogStatus.DEFERRED}:
                kind = "obsolete_equivalent"
                reason = "Very similar title to an obsolete, deferred, or rejected backlog item."
            else:
                kind = "likely_duplicate"
                reason = "Very similar title to an existing backlog item."
        elif (title_similarity >= 0.45 and scope_overlap >= 0.25) or outcome_overlap >= 0.55:
            kind = "conflicting_scope"
            reason = "Similar goal and scope to an existing backlog item."
        elif out_of_scope_overlap >= 0.4 and title_similarity >= 0.3:
            kind = "conflicting_scope"
            reason = "The new item's out-of-scope area overlaps with an existing item's body."

        if kind:
            matches.append(
                BacklogRefinementMatch(
                    item_id=item.item_id,
                    title=item.title,
                    status=item.status.value,
                    kind=kind,
                    reason=reason,
                )
            )

    matches.sort(key=lambda item: (item.kind, item.item_id))
    return tuple(matches)


def format_backlog_refinement_review(
    proposal: BacklogRefinementProposal,
    *,
    limit: int | None = None,
) -> str:
    """Render a phone-friendly review block for humans."""

    lines = [
        "Backlog refinement ready",
        f"ID: {proposal.draft.item_id}",
        f"Title: {proposal.draft.title}",
        f"Priority: {proposal.draft.priority}",
        f"Approval required: {'yes' if proposal.draft.approval_required else 'no'}",
        f"Research required: {'yes' if proposal.draft.research_required else 'no'}",
        f"Skill: {proposal.skill_path}",
    ]
    if proposal.matches:
        lines.append("Matches:")
        lines.extend(
            f"- {match.item_id} ({match.status}) [{match.kind}] {match.reason}"
            for match in proposal.matches[:8]
        )
    else:
        lines.append("Matches: none found")
    if proposal.blocked:
        lines.append("Blocked: yes")
        lines.append("Resolve the overlap before writing this item.")
    else:
        lines.append("Blocked: no")
        lines.append("Reply /approve to add it or /reject to discard it.")

    rendered = "\n".join(lines)
    if limit is not None and len(rendered) > limit:
        return rendered[: max(0, limit - 3)] + "..."
    return rendered


def append_approved_backlog_refinement(
    proposal: BacklogRefinementProposal,
    repository: BacklogRepositoryProtocol,
) -> BacklogItem:
    """Persist an already-approved backlog refinement draft."""

    return repository.add_refined_item(proposal.draft)


def build_backlog_refinement_block_summary(proposal: BacklogRefinementProposal) -> str:
    """Return a concise human-safe block summary for Hub callers."""

    if not proposal.matches:
        return "Backlog refinement is blocked, but no specific matching backlog items were found."
    parts = [
        f"{match.item_id} [{match.kind}] {match.reason}"
        for match in proposal.matches[:8]
    ]
    return "Backlog refinement blocked by matching backlog work: " + "; ".join(parts)


def render_backlog_refinement_draft_text(proposal: BacklogRefinementProposal) -> str:
    """Render the refined draft in the repository's canonical human format."""

    return render_backlog_refinement_draft(proposal.draft)


def _normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _significant_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _token_overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _draft_to_payload(draft: BacklogRefinementDraft) -> dict[str, object]:
    return {
        "item_id": draft.item_id,
        "title": draft.title,
        "creator": draft.creator,
        "item_type": draft.item_type,
        "epic": draft.epic,
        "priority": draft.priority,
        "size": draft.size,
        "approval_required": draft.approval_required,
        "approval_reason": draft.approval_reason,
        "problem": draft.problem,
        "desired_outcome": draft.desired_outcome,
        "scope": list(draft.scope),
        "out_of_scope": list(draft.out_of_scope),
        "acceptance_criteria": list(draft.acceptance_criteria),
        "duplicate_check_result": draft.duplicate_check_result,
        "stale_check_result": draft.stale_check_result,
        "already_done_check_result": draft.already_done_check_result,
        "research_required": draft.research_required,
        "research_cache_used": list(draft.research_cache_used),
        "external_research_needed": draft.external_research_needed,
        "recommended_implementation_pattern": draft.recommended_implementation_pattern,
        "patterns_explicitly_rejected": list(draft.patterns_explicitly_rejected),
        "freshness_risk": draft.freshness_risk,
        "implementation_guidance": draft.implementation_guidance,
        "approval_risk_flags": list(draft.approval_risk_flags),
    }


def _draft_from_payload(payload: dict[str, object]) -> BacklogRefinementDraft:
    return BacklogRefinementDraft(
        item_id=str(payload["item_id"]).strip(),
        title=str(payload["title"]).strip(),
        creator=str(payload["creator"]).strip(),
        item_type=str(payload["item_type"]).strip(),
        epic=str(payload["epic"]).strip(),
        priority=str(payload["priority"]).strip(),
        size=str(payload["size"]).strip(),
        approval_required=bool(payload["approval_required"]),
        approval_reason=str(payload["approval_reason"]).strip(),
        problem=str(payload["problem"]).strip(),
        desired_outcome=str(payload["desired_outcome"]).strip(),
        scope=[str(item).strip() for item in payload["scope"] if str(item).strip()],
        out_of_scope=[
            str(item).strip() for item in payload["out_of_scope"] if str(item).strip()
        ],
        acceptance_criteria=[
            str(item).strip() for item in payload["acceptance_criteria"] if str(item).strip()
        ],
        duplicate_check_result=str(payload["duplicate_check_result"]).strip(),
        stale_check_result=str(payload["stale_check_result"]).strip(),
        already_done_check_result=str(payload["already_done_check_result"]).strip(),
        research_required=bool(payload["research_required"]),
        research_cache_used=[
            str(item).strip() for item in payload["research_cache_used"] if str(item).strip()
        ],
        external_research_needed=bool(payload["external_research_needed"]),
        recommended_implementation_pattern=str(
            payload["recommended_implementation_pattern"]
        ).strip(),
        patterns_explicitly_rejected=[
            str(item).strip()
            for item in payload["patterns_explicitly_rejected"]
            if str(item).strip()
        ],
        freshness_risk=str(payload["freshness_risk"]).strip(),
        implementation_guidance=str(payload["implementation_guidance"]).strip(),
        approval_risk_flags=[
            str(item).strip() for item in payload["approval_risk_flags"] if str(item).strip()
        ],
    )
