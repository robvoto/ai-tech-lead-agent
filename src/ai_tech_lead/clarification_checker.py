"""Clarification checker for one focused question before coding-agent instruction."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)
from .prompt_loader import CLARIFICATION_CHECK_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class ClarificationDecision:
    needs_clarification: bool
    question: str
    reason: str


def check_task_clarification(
    request: str,
    task_feedback: list[str],
    settings: AppSettings,
) -> ClarificationDecision:
    """Decide whether more info is needed before tech lead analysis."""

    if not settings.orchestrator_ai_enabled:
        return ClarificationDecision(needs_clarification=False, question="", reason="")

    try:
        return _llm_clarification_decision(request, task_feedback, settings)
    except OrchestratorLlmError as error:
        logger.warning("Clarification check LLM call failed: %s", error)
        return ClarificationDecision(needs_clarification=False, question="", reason="")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Clarification check returned invalid response: %s", error)
        return ClarificationDecision(needs_clarification=False, question="", reason="")


def _llm_clarification_decision(
    request: str,
    task_feedback: list[str],
    settings: AppSettings,
) -> ClarificationDecision:
    feedback_text = "\n".join(task_feedback) if task_feedback else ""
    prompt = render_prompt(
        CLARIFICATION_CHECK_PROMPT_KEY,
        request=request,
        task_feedback=feedback_text,
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
