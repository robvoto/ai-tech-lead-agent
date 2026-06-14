"""Build validated backlog refinement drafts from human text.

This is a standalone backlog grooming flow. It is not tied to coding execution;
it turns a rough idea into a structured backlog item with research context,
duplicate checks, and implementation guidance before any code task starts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.backlog_repository import (
    BacklogRefinementDraft,
    MarkdownBacklogRepository,
    validate_backlog_refinement_draft,
)
from ai_tech_lead.logging_setup import LOGGER_NAME
from ai_tech_lead.orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)
from ai_tech_lead.research_cache import (
    format_research_cache_for_prompt,
    load_research_cache_entries,
)

logger = logging.getLogger(LOGGER_NAME)

MAX_REFINEMENT_CONTEXT_ITEMS = 30


class BacklogRefinementBuildError(RuntimeError):
    """Raised when the orchestrator cannot create a safe backlog refinement."""


@dataclass(frozen=True)
class BacklogRefinementBuildResult:
    draft: BacklogRefinementDraft
    source: str


def build_backlog_refinement_from_text(
    *,
    text: str,
    repository: MarkdownBacklogRepository,
    settings: AppSettings,
) -> BacklogRefinementBuildResult:
    """Use the orchestrator AI to turn a rough idea into a refined backlog item."""

    normalized_text = text.strip()
    if not normalized_text:
        raise BacklogRefinementBuildError("Backlog request cannot be empty.")
    if not settings.orchestrator_ai_enabled:
        raise BacklogRefinementBuildError(
            "Orchestrator AI must be enabled before I can refine backlog items from free text."
        )

    next_item_id = repository.next_item_id("ATL")
    existing_items = repository.list_items()
    try:
        research_cache_entries = load_research_cache_entries()
        result = call_orchestrator_llm(
            prompt=_backlog_refinement_prompt(
                normalized_text,
                next_item_id,
                existing_items=existing_items[:MAX_REFINEMENT_CONTEXT_ITEMS],
                research_cache_block=format_research_cache_for_prompt(research_cache_entries),
            ),
            config=OrchestratorLlmConfig(
                model=settings.orchestrator_ai_model,
                max_output_tokens=max(settings.orchestrator_ai_max_output_tokens, 800),
                timeout_seconds=settings.orchestrator_ai_timeout_seconds,
            ),
        )
        payload = json.loads(_strip_json_fence(result.text))
        draft = _parse_backlog_refinement_payload(payload, item_id=next_item_id)
        validate_backlog_refinement_draft(
            draft,
            existing_ids={item.item_id for item in existing_items},
        )
        return BacklogRefinementBuildResult(draft=draft, source=settings.orchestrator_ai_model)
    except FileNotFoundError as error:
        logger.warning("Backlog refinement cache unavailable: %s", error)
        raise BacklogRefinementBuildError(
            "Research cache is unavailable. Read docs/research/INDEX.md "
            "and add the relevant note before refining backlog items."
        ) from error
    except (OrchestratorLlmError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        logger.warning("Backlog refinement build failed: %s", error)
        raise BacklogRefinementBuildError(
            "I could not create a valid refined backlog item from that request. "
            "Try again with a clearer goal and constraints."
        ) from error


def _parse_backlog_refinement_payload(
    payload: dict[str, Any],
    *,
    item_id: str,
) -> BacklogRefinementDraft:
    title = str(payload["title"]).strip()
    item_type = str(payload["item_type"]).strip()
    epic = str(payload["epic"]).strip()
    priority = str(payload["priority"]).strip()
    size = str(payload["size"]).strip()
    approval_required = payload["approval_required"]
    approval_reason = str(payload["approval_reason"]).strip()
    problem = str(payload["problem"]).strip()
    desired_outcome = str(payload["desired_outcome"]).strip()
    scope = _parse_string_list(payload["scope"], field_name="scope")
    out_of_scope = _parse_string_list(payload["out_of_scope"], field_name="out_of_scope")
    acceptance_criteria = _parse_string_list(
        payload["acceptance_criteria"],
        field_name="acceptance_criteria",
    )
    duplicate_check_result = str(payload["duplicate_check_result"]).strip()
    stale_check_result = str(payload["stale_check_result"]).strip()
    already_done_check_result = str(payload["already_done_check_result"]).strip()
    research_required = payload["research_required"]
    research_cache_used = _parse_string_list(
        payload["research_cache_used"],
        field_name="research_cache_used",
        allow_empty=True,
    )
    external_research_needed = payload["external_research_needed"]
    recommended_implementation_pattern = str(payload["recommended_implementation_pattern"]).strip()
    patterns_explicitly_rejected = _parse_string_list(
        payload["patterns_explicitly_rejected"],
        field_name="patterns_explicitly_rejected",
    )
    freshness_risk = str(payload["freshness_risk"]).strip()
    implementation_guidance = str(payload["implementation_guidance"]).strip()
    approval_risk_flags = _parse_string_list(
        payload["approval_risk_flags"],
        field_name="approval_risk_flags",
        allow_empty=True,
    )

    if not isinstance(approval_required, bool):
        raise ValueError("approval_required must be boolean")
    if not priority:
        raise ValueError("priority cannot be empty")
    if not isinstance(research_required, bool):
        raise ValueError("research_required must be boolean")
    if not isinstance(external_research_needed, bool):
        raise ValueError("external_research_needed must be boolean")

    return BacklogRefinementDraft(
        item_id=item_id,
        title=title,
        creator="Human",
        item_type=item_type,
        epic=epic,
        priority=priority,
        size=size,
        approval_required=approval_required,
        approval_reason=approval_reason,
        problem=problem,
        desired_outcome=desired_outcome,
        scope=scope,
        out_of_scope=out_of_scope,
        acceptance_criteria=acceptance_criteria,
        duplicate_check_result=duplicate_check_result,
        stale_check_result=stale_check_result,
        already_done_check_result=already_done_check_result,
        research_required=research_required,
        research_cache_used=research_cache_used,
        external_research_needed=external_research_needed,
        recommended_implementation_pattern=recommended_implementation_pattern,
        patterns_explicitly_rejected=patterns_explicitly_rejected,
        freshness_risk=freshness_risk,
        implementation_guidance=implementation_guidance,
        approval_risk_flags=approval_risk_flags,
    )


def _parse_string_list(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")

    items = [str(item).strip() for item in value if str(item).strip()]
    if not items and not allow_empty:
        raise ValueError(f"{field_name} must contain at least one non-empty item")
    return items


def _strip_json_fence(text: str) -> str:
    stripped_text = text.strip()
    if not stripped_text.startswith("```"):
        return stripped_text
    lines = stripped_text.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped_text


def _backlog_refinement_prompt(
    text: str,
    item_id: str,
    *,
    existing_items: list[Any],
    research_cache_block: str,
) -> str:
    existing_item_lines = [
        f"- {item.item_id} - {item.title}: {_summarize_text(item.body, limit=140)}"
        for item in existing_items
    ]
    existing_item_block = "\n".join(existing_item_lines) if existing_item_lines else "- None"
    return (
        "You are the backlog refinement assistant for the AI Technical Lead Orchestrator. "
        "Return only valid JSON. Do not include Markdown. "
        "Turn the user's rough idea into one structured backlog item. "
        "Use this exact schema: "
        '{"title": string, "item_type": string, "epic": string, '
        '"priority": string, "size": string, '
        '"approval_required": boolean, "approval_reason": string, '
        '"problem": string, "desired_outcome": string, "scope": string[], '
        '"out_of_scope": string[], "acceptance_criteria": string[], '
        '"duplicate_check_result": string, "stale_check_result": string, '
        '"already_done_check_result": string, "research_required": boolean, '
        '"research_cache_used": string[], "external_research_needed": boolean, '
        '"recommended_implementation_pattern": string, '
        '"patterns_explicitly_rejected": string[], "freshness_risk": string, '
        '"implementation_guidance": string, "approval_risk_flags": string[]}. '
        "The item must be small, specific, and implementation-ready. "
        "item_type must be one of: Story, Task, Chore, Bug. "
        "epic is a short area name such as 'AI Tech Lead Infrastructure' or 'Backlog Management'. "
        "priority must be one of: High, Medium, Low. "
        "size must be one of: XS, S, M, L, XL "
        "(effort estimate — XS=hours, S=1day, M=2-3days, L=week, XL=multi-week). "
        "research_cache_used may be empty if no cache entries were relevant. "
        "Check docs/research/INDEX.md first and reuse the cached research notes "
        "below where possible. "
        "If the cache is missing, stale, or insufficient, set "
        "external_research_needed=true and explain the freshness risk. "
        "Workbook-managed columns such as Status, Created Date, Creator, Has Goal, "
        "Has Problem, Has Outcome, Has Acceptance Criteria, Has Constraints, "
        "Missing Fields, Missing Count, Completeness, Cleanup Needed, and "
        "Notes / Cleanup Action are set by the repository code after validation. "
        "Do not try to invent those fields in the JSON; instead make the draft "
        "specific enough that the repository can populate them without guessing. "
        "Do not invent implementation details beyond the user's request and the cached research. "
        "Do not use hidden heuristics; make duplicate, stale, and already-done "
        "checks explicit in the JSON.\n\n"
        f"Backlog item ID to use: {item_id}\n"
        f"User rough idea:\n{text}\n\n"
        "Existing backlog items:\n"
        f"{existing_item_block}\n\n"
        "Research cache:\n"
        f"{research_cache_block}"
    )


def _summarize_text(text: str, *, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return normalized[: limit - 3].rstrip() + "..."
