"""Task formulator — rewrites the raw request as a clean coding-agent task description."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .prompt_loader import load_prompt
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)

logger = logging.getLogger(LOGGER_NAME)
TASK_FORMULATION_PROMPT = load_prompt("task_formulation_prompt.md")


@dataclass(frozen=True)
class FormulatedTask:
    task_description: str


def formulate_task(
    request: str,
    brief: str,
    task_feedback: list[str],
    settings: AppSettings,
) -> FormulatedTask:
    """Write a clean coding-agent task description from the raw request, brief, and clarifications."""

    if not settings.orchestrator_ai_enabled:
        return FormulatedTask(task_description=request)

    try:
        return _llm_formulate_task(request, brief, task_feedback, settings)
    except OrchestratorLlmError as error:
        logger.warning("Task formulation LLM call failed: %s", error)
        return FormulatedTask(task_description=request)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Task formulation returned invalid response: %s", error)
        return FormulatedTask(task_description=request)


def _llm_formulate_task(
    request: str,
    brief: str,
    task_feedback: list[str],
    settings: AppSettings,
) -> FormulatedTask:
    feedback_text = "\n".join(task_feedback) if task_feedback else ""
    prompt = (
        TASK_FORMULATION_PROMPT
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
    return _parse_formulated_task(payload)


def _parse_formulated_task(payload: dict[str, Any]) -> FormulatedTask:
    task_description = str(payload.get("task_description", "")).strip()
    if not task_description:
        raise ValueError("task_description cannot be empty")
    return FormulatedTask(task_description=task_description)
