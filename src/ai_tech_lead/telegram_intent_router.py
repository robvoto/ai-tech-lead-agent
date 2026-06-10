"""Classify normal Telegram text into a safe proposed orchestrator action."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import logging
from typing import Any

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.logging_setup import LOGGER_NAME
from ai_tech_lead.orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)

logger = logging.getLogger(LOGGER_NAME)


class TelegramIntentAction(StrEnum):
    ASK = "ask"
    BACKLOG_PROPOSAL = "backlog_proposal"
    CODE_TASK = "code_task"
    STATUS = "status"
    HELP = "help"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class TelegramIntent:
    action: TelegramIntentAction
    summary: str
    response: str


def route_plain_text_intent(*, text: str, settings: AppSettings) -> TelegramIntent:
    """Route plain Telegram text without executing risky work.

    This is intentionally a proposal layer. It may classify intent and explain the
    next safe action, but it must not run the configured coding agent or mutate the backlog.
    """

    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("Telegram message cannot be empty.")

    if not settings.orchestrator_ai_enabled:
        return TelegramIntent(
            action=TelegramIntentAction.UNCLEAR,
            summary="AI routing is disabled",
            response=(
                "I can route normal text when orchestrator AI is enabled. "
                "For now use /code for a coding task, /run to implement a backlog item, or /help."
            ),
        )

    try:
        result = call_orchestrator_llm(
            prompt=_intent_prompt(normalized_text),
            config=OrchestratorLlmConfig(
                model=settings.orchestrator_ai_model,
                max_output_tokens=settings.orchestrator_ai_max_output_tokens,
                timeout_seconds=settings.orchestrator_ai_timeout_seconds,
            ),
        )
        return _parse_intent_payload(json.loads(_strip_json_fence(result.text)))
    except (OrchestratorLlmError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        logger.warning("Telegram intent router failed: %s", error)
        return TelegramIntent(
            action=TelegramIntentAction.UNCLEAR,
            summary="Intent router failed",
            response=(
                "I could not confidently classify that. Use /code for a coding task, "
                "/run to implement a backlog item, or /help."
            ),
        )


def _parse_intent_payload(payload: dict[str, Any]) -> TelegramIntent:
    action = TelegramIntentAction(str(payload["action"]).strip())
    summary = str(payload.get("summary", "")).strip()
    response = str(payload.get("response", "")).strip()
    if not summary:
        raise ValueError("intent summary cannot be empty")
    if not response:
        raise ValueError("intent response cannot be empty")
    return TelegramIntent(action=action, summary=summary, response=response)


def _strip_json_fence(text: str) -> str:
    stripped_text = text.strip()
    if not stripped_text.startswith("```"):
        return stripped_text
    lines = stripped_text.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped_text


def _intent_prompt(text: str) -> str:
    return (
        "You are the Telegram intent router for the AI Technical Lead Orchestrator. "
        "Return only valid JSON. Do not include Markdown. "
        "Classify the user's message without executing anything. "
        "Allowed actions: ask, backlog_proposal, code_task, status, help, unclear. "
        "Use code_task only when the user clearly wants a code/doc/test/runtime change. "
        "Use backlog_proposal when the user wants to capture work for later. "
        "Use ask when the user is asking a question or wants an explanation. "
        "Use unclear when intent is ambiguous. "
        "Schema: {\"action\": string, \"summary\": string, \"response\": string}. "
        "For code_task, the response must say this requires /code or approval before any coding workflow. "
        "For backlog_proposal, the response must say a backlog item can be proposed but needs confirmation.\n\n"
        f"User message:\n{text}"
    )
