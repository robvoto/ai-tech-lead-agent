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
from .profile_override_store import resolve_profile_with_override
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
    code_recon_report: str = "",
    project_guidance: list[str] | None = None,
) -> TechLeadAnalysis:
    """Produce a task statement and high-level technical direction for the coding agent.

    ``project_guidance`` is the target project's own bounded guidance (AGENTS.md,
    docs/INDEX.md, .skills/INDEX.md, and at most one matching skill — see
    project_guidance_discovery.py), distinct from ``research_evidence``: it answers
    "what does this repo already say about working in it", not "is there a domain
    knowledge gap". It may be empty — that's the normal case when a project has no
    project pack yet, and analysis proceeds on AI Tech Lead's own core guidance.
    """

    if not settings.orchestrator_ai_enabled:
        return TechLeadAnalysis(task_statement=request, tech_direction="")

    try:
        return _llm_analyse_task(
            request,
            task_feedback,
            approval_reason,
            research_evidence,
            settings,
            code_recon_report,
            project_guidance or [],
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
    code_recon_report: str = "",
    project_guidance: list[str] | None = None,
) -> TechLeadAnalysis:
    feedback_text = "\n".join(task_feedback) if task_feedback else "(none)"
    evidence_text = (
        "Research evidence:\n" + "\n".join(f"- {e}" for e in research_evidence)
        if research_evidence
        else ""
    )
    code_recon_text = (
        f"Code look report:\n{code_recon_report}" if code_recon_report.strip() else ""
    )
    project_guidance_text = (
        "Target project's own guidance:\n" + "\n".join(f"- {g}" for g in project_guidance)
        if project_guidance
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
        code_recon=code_recon_text,
        project_guidance=project_guidance_text,
    )
    profile = resolve_profile_with_override("tech_lead_analysis", settings=settings)
    config = OrchestratorLlmConfig(
        model=profile.model,
        max_output_tokens=profile.max_output_tokens,
        timeout_seconds=settings.orchestrator_ai_timeout_seconds,
        reasoning_effort=profile.reasoning_effort,
        purpose="tech_lead_analysis",
        profile_name=profile.name,
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
        tier=profile.tier,
    )


def _parse_analysis(payload: dict[str, Any]) -> TechLeadAnalysis:
    task_statement = str(payload.get("task_statement", "")).strip()
    tech_direction = str(payload.get("tech_direction", "")).strip()
    if not task_statement:
        raise ValueError("task_statement cannot be empty")
    return TechLeadAnalysis(task_statement=task_statement, tech_direction=tech_direction)
