"""Clarification checker — asks the orchestrator LLM one focused question before writing the coding agent instruction."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from .app_settings import AppSettings, load_settings
from .logging_setup import LOGGER_NAME
from .prompt_loader import load_prompt
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)

logger = logging.getLogger(LOGGER_NAME)
CLARIFICATION_CHECK_PROMPT = load_prompt("clarification_check_prompt.md")


@dataclass(frozen=True)
class ClarificationDecision:
    needs_clarification: bool
    question: str
    reason: str


def check_task_clarification(
    request: str,
    brief: str,
    task_feedback: list[str],
    settings: AppSettings,
) -> ClarificationDecision:
    """Decide whether the orchestrator needs more info before writing the coding agent instruction."""

    if not settings.orchestrator_ai_enabled:
        return ClarificationDecision(needs_clarification=False, question="", reason="")

    try:
        return _llm_clarification_decision(request, brief, task_feedback, settings)
    except OrchestratorLlmError as error:
        logger.warning("Clarification check LLM call failed: %s", error)
        return ClarificationDecision(needs_clarification=False, question="", reason="")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Clarification check returned invalid response: %s", error)
        return ClarificationDecision(needs_clarification=False, question="", reason="")


def _llm_clarification_decision(
    request: str,
    brief: str,
    task_feedback: list[str],
    settings: AppSettings,
) -> ClarificationDecision:
    feedback_text = "\n".join(task_feedback) if task_feedback else ""
    prompt = (
        CLARIFICATION_CHECK_PROMPT
        .replace("{request}", request)
        .replace("{brief}", brief)
        .replace("{task_feedback}", feedback_text)
    )
    result = call_orchestrator_llm(
        prompt=prompt,
        config=OrchestratorLlmConfig(
            model=settings.orchestrator_ai_model,
            max_output_tokens=settings.orchestrator_ai_max_output_tokens,
            timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        ),
    )
    payload = json.loads(result.text)
    return _parse_clarification_payload(payload)


def _parse_clarification_payload(payload: dict[str, Any]) -> ClarificationDecision:
    needs_clarification = payload["needs_clarification"]
    question = str(payload.get("question", "")).strip()
    reason = str(payload.get("reason", "")).strip()

    if not isinstance(needs_clarification, bool):
        raise ValueError("needs_clarification must be boolean")
    if needs_clarification and not question:
        raise ValueError("question is required when needs_clarification is true")

    return ClarificationDecision(
        needs_clarification=needs_clarification,
        question=question,
        reason=reason,
    )
