"""Build validated backlog drafts from human text."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.backlog_repository import (
    BacklogDraft,
    MarkdownBacklogRepository,
    validate_backlog_draft,
)
from ai_tech_lead.logging_setup import LOGGER_NAME
from ai_tech_lead.orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)

logger = logging.getLogger(LOGGER_NAME)


class BacklogDraftBuildError(RuntimeError):
    """Raised when the orchestrator cannot create a safe backlog draft."""


@dataclass(frozen=True)
class BacklogDraftBuildResult:
    draft: BacklogDraft
    source: str


def build_backlog_draft_from_text(
    *,
    text: str,
    repository: MarkdownBacklogRepository,
    settings: AppSettings,
) -> BacklogDraftBuildResult:
    """Use the orchestrator AI to turn a human request into a backlog draft."""

    normalized_text = text.strip()
    if not normalized_text:
        raise BacklogDraftBuildError("Backlog request cannot be empty.")
    if not settings.orchestrator_ai_enabled:
        raise BacklogDraftBuildError(
            "Orchestrator AI must be enabled before I can draft backlog items from free text."
        )

    next_item_id = repository.next_item_id("ATL")
    try:
        result = call_orchestrator_llm(
            prompt=_backlog_draft_prompt(normalized_text, next_item_id),
            config=OrchestratorLlmConfig(
                model=settings.orchestrator_ai_model,
                max_output_tokens=max(settings.orchestrator_ai_max_output_tokens, 500),
                timeout_seconds=settings.orchestrator_ai_timeout_seconds,
            ),
        )
        payload = json.loads(_strip_json_fence(result.text))
        draft = _parse_backlog_draft_payload(payload, item_id=next_item_id)
        validate_backlog_draft(draft, existing_ids={item.item_id for item in repository.list_items()})
        return BacklogDraftBuildResult(draft=draft, source=settings.orchestrator_ai_model)
    except (OrchestratorLlmError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        logger.warning("Backlog draft build failed: %s", error)
        raise BacklogDraftBuildError(
            "I could not create a valid backlog item from that request. Try again with a clearer goal and constraints."
        ) from error


def _parse_backlog_draft_payload(payload: dict[str, Any], *, item_id: str) -> BacklogDraft:
    title = str(payload["title"]).strip()
    approval_required = payload["approval_required"]
    approval_reason = str(payload["approval_reason"]).strip()
    goal = str(payload["goal"]).strip()
    constraints_value = payload["constraints"]

    if not isinstance(approval_required, bool):
        raise ValueError("approval_required must be boolean")
    if not isinstance(constraints_value, list):
        raise ValueError("constraints must be a list")

    constraints = [str(item).strip() for item in constraints_value if str(item).strip()]
    return BacklogDraft(
        item_id=item_id,
        title=title,
        approval_required=approval_required,
        approval_reason=approval_reason,
        goal=goal,
        constraints=constraints,
    )


def _strip_json_fence(text: str) -> str:
    stripped_text = text.strip()
    if not stripped_text.startswith("```"):
        return stripped_text
    lines = stripped_text.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped_text


def _backlog_draft_prompt(text: str, item_id: str) -> str:
    return (
        "You are the backlog assistant for the AI Technical Lead Orchestrator. "
        "Return only valid JSON. Do not include Markdown. "
        "Create one small, implementation-ready backlog item from the user's request. "
        "Use this exact schema: "
        "{\"title\": string, \"approval_required\": boolean, \"approval_reason\": string, "
        "\"goal\": string, \"constraints\": string[]}. "
        "Keep the title under 120 characters. "
        "Use approval_required=true for graph, Telegram, AI, settings, storage, execution, security, broad, or risky changes. "
        "Use concrete constraints. Do not invent implementation details beyond the user's request.\n\n"
        f"Backlog item ID to use: {item_id}\n"
        f"User request:\n{text}"
    )
