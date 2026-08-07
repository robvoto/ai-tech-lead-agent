"""Decide whether a task needs Codex to look at code before ATL analyses it.

Scales effort to complexity: most tasks either don't touch existing code
or are simple enough that a code look adds cost without adding value. This
answers that one narrow question before spending a real Codex call on it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings, load_settings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)
from .prompt_loader import CODE_LOOK_NEED_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class CodeLookNeedDecision:
    """Structured decision written back into LangGraph state."""

    needs_code_look: bool
    reason: str


def check_code_look_need(request: str) -> CodeLookNeedDecision:
    """Decide if this task needs a real Codex look at code before analysis.

    Fails open toward skipping (no code look) when AI is disabled or the
    call errors, since this is a cost-saving enhancement, not a safety gate.
    """

    normalized_request = request.strip()
    if not normalized_request:
        raise ValueError("Code look need check request cannot be empty.")

    settings = load_settings()
    if not settings.orchestrator_ai_enabled:
        return CodeLookNeedDecision(needs_code_look=False, reason="Orchestrator AI disabled.")

    try:
        return _llm_code_look_decision(normalized_request, settings)
    except OrchestratorLlmError as error:
        logger.warning("Orchestrator AI code-look check unavailable: %s", error)
        return CodeLookNeedDecision(
            needs_code_look=False,
            reason=f"Code-look check failed open (AI unavailable): {error}",
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Invalid orchestrator AI code-look response: %s", error)
        return CodeLookNeedDecision(
            needs_code_look=False,
            reason=f"Code-look check failed open (invalid AI response): {error}",
        )


def _llm_code_look_decision(request: str, settings: AppSettings) -> CodeLookNeedDecision:
    prompt = render_prompt(CODE_LOOK_NEED_PROMPT_KEY, request=request)
    result = call_orchestrator_llm(
        prompt=prompt,
        config=OrchestratorLlmConfig(
            model=settings.orchestrator_ai_model,
            max_output_tokens=settings.orchestrator_ai_max_output_tokens,
            timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        ),
    )
    payload: dict[str, Any] = json.loads(result.text)
    needs_code_look = payload["needs_code_look"]
    reason = str(payload["reason"]).strip()
    if not isinstance(needs_code_look, bool):
        raise ValueError("needs_code_look must be boolean")
    if not reason:
        raise ValueError("reason cannot be empty")
    return CodeLookNeedDecision(needs_code_look=needs_code_look, reason=reason)
