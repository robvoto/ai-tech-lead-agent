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
from .profile_override_store import resolve_profile_with_override
from .prompt_loader import OPERATOR_QUESTION_ANSWER_PROMPT_KEY, render_prompt
from .target_project_context import TargetProjectContext

logger = logging.getLogger(LOGGER_NAME)

_FALLBACK_ANSWER = (
    "I couldn't answer that question right now. You can still approve, "
    "request changes, or cancel."
)


def answer_operator_question(
    *,
    question: str,
    request: str,
    target_project_context: TargetProjectContext | None = None,
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
    if target_project_context is None:
        project_text = "(this project)"
    else:
        project_text = (
            target_project_context.project_identity
            or target_project_context.project_root
            or "(this project)"
        )

    prompt = render_prompt(
        OPERATOR_QUESTION_ANSWER_PROMPT_KEY,
        question=question,
        request=request,
        project=project_text,
        code_context=code_context or "(none)",
        research_evidence=evidence_text,
    )

    try:
        profile = resolve_profile_with_override("operator_question", settings=settings)
        result = call_orchestrator_llm(
            prompt=prompt,
            config=OrchestratorLlmConfig(
                model=profile.model,
                max_output_tokens=profile.max_output_tokens,
                timeout_seconds=settings.orchestrator_ai_timeout_seconds,
                reasoning_effort=profile.reasoning_effort,
                purpose="operator_question",
                profile_name=profile.name,
            ),
        )
    except OrchestratorLlmError as error:
        logger.warning("Operator question answer LLM call failed: %s", error)
        return _FALLBACK_ANSWER

    answer = result.text.strip()
    return answer or _FALLBACK_ANSWER
