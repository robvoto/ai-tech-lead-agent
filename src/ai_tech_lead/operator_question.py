"""Answer a human operator's question during the approval interrupt.

Grounds the answer in the actual request, resolved project context, bounded
code context, and research evidence already gathered — never invents facts.
Fails soft (a plain "couldn't answer" message) rather than raising, since
this runs inside a human-approval gate and must never crash the node.
"""

from __future__ import annotations

import logging

from .app_settings import AppSettings
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    call_orchestrator_llm,
)
from .prompt_loader import OPERATOR_QUESTION_ANSWER_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)

_FALLBACK_ANSWER = (
    "I couldn't answer that question right now. You can still approve, "
    "request changes, or cancel."
)


def answer_operator_question(
    *,
    question: str,
    request: str,
    resolved_project_identity: str,
    resolved_project_root: str,
    code_context: str,
    research_evidence: list[str],
    settings: AppSettings,
) -> str:
    """Return a grounded answer to the operator's question, or a fallback message."""

    if not settings.orchestrator_ai_enabled:
        return (
            "AI is disabled right now, so I can't answer questions. "
            "You can still approve, request changes, or cancel."
        )

    evidence_text = (
        "\n".join(f"- {item}" for item in research_evidence) if research_evidence else "(none)"
    )
    project_text = resolved_project_identity or resolved_project_root or "(this project)"

    prompt = render_prompt(
        OPERATOR_QUESTION_ANSWER_PROMPT_KEY,
        question=question,
        request=request,
        project=project_text,
        code_context=code_context or "(none)",
        research_evidence=evidence_text,
    )

    try:
        result = call_orchestrator_llm(
            prompt=prompt,
            config=OrchestratorLlmConfig(
                model=settings.orchestrator_ai_model,
                max_output_tokens=settings.orchestrator_ai_max_output_tokens,
                timeout_seconds=settings.orchestrator_ai_timeout_seconds,
            ),
        )
    except OrchestratorLlmError as error:
        logger.warning("Operator question answer LLM call failed: %s", error)
        return _FALLBACK_ANSWER

    answer = result.text.strip()
    return answer or _FALLBACK_ANSWER
