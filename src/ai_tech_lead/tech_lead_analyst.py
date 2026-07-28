"""Tech lead analysis: formulate the task and set high-level technical direction."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .app_settings import AppSettings
from .llm_json import call_llm_for_json
from .logging_setup import LOGGER_NAME
from .orchestrator_llm import OrchestratorLlmConfig, OrchestratorLlmError
from .prompt_loader import TECH_LEAD_ANALYSIS_PROMPT_KEY, render_prompt

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class TechLeadAnalysis:
    task_statement: str
    tech_direction: str


def analyse_task(
    request: str,
    task_feedback: list[str],
    approval_reason: str,
    research_evidence: list[str],
    settings: AppSettings,
) -> TechLeadAnalysis:
    """Produce a task statement and high-level technical direction for the coding agent."""

    if not settings.orchestrator_ai_enabled:
        return TechLeadAnalysis(task_statement=request, tech_direction="")

    try:
        return _llm_analyse_task(
            request, task_feedback, approval_reason, research_evidence, settings
        )
    except OrchestratorLlmError as error:
        logger.warning("Tech lead analysis LLM call failed: %s", error)
        return TechLeadAnalysis(task_statement=request, tech_direction="")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("Tech lead analysis returned invalid response: %s", error)
        return TechLeadAnalysis(task_statement=request, tech_direction="")


def _llm_analyse_task(
    request: str,
    task_feedback: list[str],
    approval_reason: str,
    research_evidence: list[str],
    settings: AppSettings,
) -> TechLeadAnalysis:
    feedback_text = "\n".join(task_feedback) if task_feedback else "(none)"
    evidence_text = (
        "Research evidence:\n" + "\n".join(f"- {e}" for e in research_evidence)
        if research_evidence
        else ""
    )
    approval_note = f"Approval reason: {approval_reason}" if approval_reason else ""
    watched_dirs = "\n".join(f"- {d}" for d in settings.watched_directories) or "(none)"
    constraints = "\n".join(f"- {c}" for c in settings.brief_constraints) or "(none)"
    acceptance = "\n".join(f"- {a}" for a in settings.acceptance_criteria) or "(none)"
    risk_notes = "\n".join(f"- {r}" for r in settings.risk_notes) or "(none)"

    prompt = render_prompt(
        TECH_LEAD_ANALYSIS_PROMPT_KEY,
        request=request,
        task_feedback=feedback_text,
        approval_note=approval_note,
        watched_directories=watched_dirs,
        constraints=constraints,
        acceptance_criteria=acceptance,
        risk_notes=risk_notes,
        research_evidence=evidence_text,
    )
    config = OrchestratorLlmConfig(
        model=settings.orchestrator_ai_model,
        max_output_tokens=settings.orchestrator_ai_max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
    )

    def _log_metric(result: Any) -> None:
        logger.info(
            "Tech lead analysis LLM: in=%d out=%d total=%d cost_total=$%.5f",
            result.tokens_in,
            result.tokens_out,
            result.tokens_in + result.tokens_out,
            result.cost_usd,
        )

    return call_llm_for_json(
        prompt=prompt,
        config=config,
        error_label="Tech lead analysis response",
        parse=_parse_analysis,
        on_result=_log_metric,
    )


def _parse_analysis(payload: dict[str, Any]) -> TechLeadAnalysis:
    task_statement = str(payload.get("task_statement", "")).strip()
    tech_direction = str(payload.get("tech_direction", "")).strip()
    if not task_statement:
        raise ValueError("task_statement cannot be empty")
    return TechLeadAnalysis(task_statement=task_statement, tech_direction=tech_direction)
