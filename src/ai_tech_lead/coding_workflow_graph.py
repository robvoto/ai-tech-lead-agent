"""Small LangGraph workflow for producing a safe coding-agent instruction.

This module stays focused on the graph itself so LangGraph Studio can import it
without the local CLI runner getting in the way.
"""

import hashlib
import logging
import operator
import re
from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, TypeVar, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .app_settings import load_settings
from .backlog_repository import BacklogValidationError
from .backlog_runtime_store import BacklogRuntimeStore
from .backlog_sheets_repository import (
    BacklogSheetLayout,
    BacklogSourceUnavailableError,
    repository_for,
)
from .code_look_checker import check_code_look_need
from .coding_agent_contract import normalize_coding_agent_result
from .coding_agent_runner import (
    CodingAgentCancellationToken,
    GitPreflightResult,
    run_coding_agent,
    run_git_preflight,
)
from .coding_agent_tier_profiles import DEFAULT_CODING_AGENT_TIER, resolve_tier_args
from .completion_evidence import capture_completion_evidence
from .completion_verifier import CompletionVerificationUnavailable, verify_completion
from .git_lifecycle import (
    GitLifecycleBlocked,
    GitLifecycleError,
    TaskGitState,
    commit_and_push_task_branch,
    integrate_task_branch,
    not_in_main_status,
    prepare_task_worktree,
    validate_integrated_git_result,
)
from .implementation_review import (
    IMPLEMENTATION_REVIEW_MAX_CORRECTIONS,
    STATUS_FINDINGS as IMPLEMENTATION_REVIEW_FINDINGS,
    STATUS_FINDINGS_UNRESOLVED as IMPLEMENTATION_REVIEW_FINDINGS_UNRESOLVED,
    STATUS_SKIPPED as IMPLEMENTATION_REVIEW_SKIPPED,
    matched_risk_keywords,
    review_required,
    run_implementation_review,
)
from .instruction_assembler import build_agent_instruction, selected_instruction_hashes
from .logging_setup import LOGGER_NAME
from .operator_question import answer_operator_question
from .plan_reviewer import PlanReviewUnavailable, review_plan
from .project_guidance_discovery import discover_project_guidance
from .project_guidance_governance import review_project_guidance
from .prompt_loader import (
    CODE_RECON_INSTRUCTION_PROMPT_KEY,
    PLAN_REQUEST_INSTRUCTION_PROMPT_KEY,
    render_prompt,
)
from .request_context import resolve_request_context
from .request_relevance import classify_request_relevance
from .research_cache import save_online_source_to_cache
from .research_checker import check_research_requirements
from .research_code_context import collect_code_context, format_code_context_for_prompt
from .research_discovery import discover_official_source
from .research_policy import select_research_policy
from .research_sources import collect_online_research_sources
from .risk_reviewer import review_task_risk
from .run_budget import RunBudgetExceededError, RunBudgetTracker, use_run_budget
from .target_project_context import BacklogItemContext, BacklogProjectContext, TargetProjectContext
from .tech_lead_analyst import analyse_task
from .validation_command_discovery import (
    discover_validation_command,
    sanitise_validation_command,
)

APPROVAL_MAX_REVISION_CYCLES = 5
COMPLETION_VERIFICATION_MAX_CORRECTIONS = 1
CONTEXT_CLARIFICATION_MAX_RETRIES = 1
VALIDATION_COMMAND_MAX_RETRIES = 1

T = TypeVar("T")

logger = logging.getLogger(LOGGER_NAME)

LOG_SEPARATOR = "----------------------------------------"


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "1_read_and_classify_request"
    PROJECT_SCOPE_DECISION = "1a_decide_project_scope"
    RESOLVE_CONTEXT = "1b Find Project Context"
    CONTEXT_CLARIFICATION_INTERRUPT = "1b_context_clarification_interrupt"
    CHECK_CODE_LOOK_NEED = "1c_check_if_code_look_needed"
    CODEX_READS_CODE = "1c1_codex_reads_code"
    CHECK_PROJECT_GUIDANCE = "1d_check_project_guidance"
    PROJECT_GUIDANCE_INTERRUPT = "1d1_project_guidance_interrupt"
    CHECK_RESEARCH = "2a_check_research"
    DISCOVER_RESEARCH_SOURCE = "2a1_discover_research_source"
    RESEARCH_INTERRUPT = "2a_research_interrupt"
    COLLECT_RESEARCH_EVIDENCE = "2b_collect_research_evidence"
    REVIEW_RISK = "3_review_risk"
    TECH_LEAD_ANALYSE = "2 Analyse Task"
    APPROVAL_INTERRUPT = "4_approval_interrupt"
    REQUEST_PLAN = "5b_request_plan"
    REVIEW_PLAN = "5c_review_plan"
    PLAN_INTERRUPT = "5d_plan_interrupt"
    DISCOVER_VALIDATION_COMMAND = "5e_discover_validation_command"
    VALIDATION_COMMAND_INTERRUPT = "5e1_validation_command_interrupt"
    CREATE_AGENT_INSTRUCTION = "5_create_agent_instruction"
    RUN_CODING_AGENT = "6_run_coding_agent"
    FAILURE_INTERRUPT = "6b Handle Coding Failure"
    CAPTURE_VALIDATION_EVIDENCE = "6b1_capture_validation_evidence"
    IMPLEMENTATION_REVIEW = "6b2_implementation_review"
    VERIFY_COMPLETION = "6c_verify_completion"
    RUN_BUDGET_INTERRUPT = "6c1_run_budget_interrupt"
    COMPLETION_VERIFICATION_INTERRUPT = "6d_completion_verification_interrupt"
    FINALIZE_TASK_BRANCH = "6e_finalize_task_branch"
    INTEGRATION_APPROVAL = "6f_integration_approval"
    END_NODE = "7_end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow."""

    request_id: str
    request: str
    bounded_request: str
    target_project_context: dict[str, Any] | None
    atl_relevant: bool
    atl_relevance_reason: str
    project_scope: str
    resolved_project_identity: str
    resolved_project_root: str
    resolved_resource_references: list[str]
    unresolved_references: list[str]
    context_resolution_evidence: list[str]
    backlog_item_not_found_reason: str
    context_clarification_answer: str
    context_clarification_retry_count: int
    context_clarification_exhausted: bool
    research_checked: bool
    code_look_needed: bool
    code_look_need_reason: str
    code_recon_report: str
    project_guidance_notes: list[str]
    project_guidance_status: str
    project_guidance_requires_review: bool
    project_guidance_summary: str
    project_guidance_related_locations: list[str]
    project_guidance_proposed_change: str
    project_guidance_reason: str
    project_guidance_rejected_summary: str
    project_guidance_paths: list[str]
    project_guidance_hash: str
    project_guidance_changed_on_resume: bool
    selected_skill_paths: list[str]
    selected_skill_hashes: dict[str, str]
    rubric_status: str
    brief: str
    force_approval: bool
    orchestrator_input_required: bool
    orchestrator_input_reason: str
    orchestrator_input_question: str
    orchestrator_run_max_calls: int | None
    orchestrator_run_max_tokens: int | None
    orchestrator_run_max_cost_usd: float | None
    orchestrator_calls_used: int
    orchestrator_tokens_in_used: int
    orchestrator_tokens_out_used: int
    orchestrator_tokens_used: int
    orchestrator_cost_usd_used: float
    run_budget_exceeded: bool
    run_budget_exceeded_reason: str
    run_budget_resume_node: str
    run_budget_interactive: bool
    run_budget_terminated: bool
    task_feedback: Annotated[list[str], operator.add]
    needs_approval: bool
    approval_reason: str
    risk_level: str
    approved: bool
    approved_by: str
    approval_action: str
    approval_revision_count: int
    approval_last_question: str
    approval_last_answer: str
    research_evidence_required: bool
    research_gap_question: str
    discovered_source_url: str
    discovered_source_title: str
    research_source_titles: list[str]
    research_source_locations: list[str]
    research_source_summaries: list[str]
    research_policy_profiles: list[str]
    research_trusted_domains: list[str]
    research_seed_urls: list[str]
    online_research_approved: bool
    formulated_task: str
    plan_text: str
    plan_agent_stderr: str
    plan_agent_returncode: int | None
    plan_approved: bool
    plan_review_reason: str
    plan_correction: str
    plan_rejection_count: int
    plan_needs_human_review: bool
    coding_agent_tier: str
    agent_instruction: str
    git_preflight_status: str
    git_preflight_branch: str
    git_preflight_head: str
    git_preflight_dirty_paths: tuple[str, ...]
    git_preflight_reason: str
    git_lifecycle_enabled: bool
    git_task_canonical_root: str
    git_task_worktree: str
    git_task_branch: str
    git_task_base_sha: str
    git_task_tip_sha: str
    git_task_commit_sha: str
    git_task_branch_pushed: bool
    git_main_status: str
    git_main_sha: str
    git_integration_status: str
    git_integration_reason: str
    coding_agent_result: str
    coding_agent_success: bool
    coding_agent_changed_files: tuple[str, ...]
    restart_required: bool
    coding_agent_retry_count: int
    coding_agent_correction: str
    coding_agent_command: str
    coding_agent_returncode: int | None
    coding_agent_timed_out: bool
    coding_agent_performed_by: str
    coding_agent_validation: str
    validation_command: str
    validation_command_source: str
    validation_command_retry_count: int
    validation_command_exhausted: bool
    diff_summary: str
    git_changed_files: list[str]
    validation_passed: bool | None
    validation_output_tail: str
    out_of_scope_paths: list[str]
    review_status: str
    review_findings: list[str]
    review_correction: str
    review_agent_performed_by: str
    review_correction_count: int
    verification_status: str
    verification_reason: str
    verification_correction: str
    verification_attempt_count: int


def build_initial_graph_state(
    request: str,
    *,
    request_id: str = "",
    force_approval: bool = False,
    approved: bool = False,
    approved_by: str = "",
    approval_reason: str = "Risk review has not run yet.",
    online_research_approved: bool = False,
    task_feedback: list[str] | None = None,
    target_project_context: TargetProjectContext | None = None,
) -> GraphState:
    """Return one canonical initial state for the coding workflow graph."""

    return {
        "request_id": request_id,
        "request": request,
        "bounded_request": request,
        "target_project_context": (
            target_project_context.to_payload() if target_project_context is not None else None
        ),
        "atl_relevant": True,
        "atl_relevance_reason": "",
        "project_scope": "",
        "resolved_project_identity": "",
        "resolved_project_root": "",
        "resolved_resource_references": [],
        "unresolved_references": [],
        "context_resolution_evidence": [],
        "backlog_item_not_found_reason": "",
        "context_clarification_answer": "",
        "context_clarification_retry_count": 0,
        "context_clarification_exhausted": False,
        "research_checked": False,
        "code_look_needed": False,
        "code_look_need_reason": "",
        "code_recon_report": "",
        "project_guidance_notes": [],
        "project_guidance_status": "",
        "project_guidance_requires_review": False,
        "project_guidance_summary": "",
        "project_guidance_related_locations": [],
        "project_guidance_proposed_change": "",
        "project_guidance_reason": "",
        "project_guidance_rejected_summary": "",
        "project_guidance_paths": [],
        "project_guidance_hash": "",
        "project_guidance_changed_on_resume": False,
        "selected_skill_paths": [],
        "selected_skill_hashes": {},
        "rubric_status": "",
        "brief": "",
        "force_approval": force_approval,
        "orchestrator_input_required": False,
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
        "orchestrator_run_max_calls": None,
        "orchestrator_run_max_tokens": None,
        "orchestrator_run_max_cost_usd": None,
        "orchestrator_calls_used": 0,
        "orchestrator_tokens_in_used": 0,
        "orchestrator_tokens_out_used": 0,
        "orchestrator_tokens_used": 0,
        "orchestrator_cost_usd_used": 0.0,
        "run_budget_exceeded": False,
        "run_budget_exceeded_reason": "",
        "run_budget_resume_node": "",
        "run_budget_interactive": False,
        "run_budget_terminated": False,
        "task_feedback": list(task_feedback or []),
        "needs_approval": False,
        "approval_reason": approval_reason,
        "risk_level": "",
        "approved": approved,
        "approved_by": approved_by,
        "approval_action": "",
        "approval_revision_count": 0,
        "approval_last_question": "",
        "approval_last_answer": "",
        "research_evidence_required": False,
        "research_gap_question": "",
        "discovered_source_url": "",
        "discovered_source_title": "",
        "research_source_titles": [],
        "research_source_locations": [],
        "research_source_summaries": [],
        "research_policy_profiles": [],
        "research_trusted_domains": [],
        "research_seed_urls": [],
        "online_research_approved": online_research_approved,
        "formulated_task": "",
        "plan_text": "",
        "plan_agent_stderr": "",
        "plan_agent_returncode": None,
        "plan_approved": False,
        "plan_review_reason": "",
        "plan_correction": "",
        "plan_rejection_count": 0,
        "plan_needs_human_review": False,
        "coding_agent_tier": DEFAULT_CODING_AGENT_TIER,
        "agent_instruction": "",
        "git_preflight_status": "",
        "git_preflight_branch": "",
        "git_preflight_head": "",
        "git_preflight_dirty_paths": (),
        "git_preflight_reason": "",
        "git_lifecycle_enabled": False,
        "git_task_canonical_root": "",
        "git_task_worktree": "",
        "git_task_branch": "",
        "git_task_base_sha": "",
        "git_task_tip_sha": "",
        "git_task_commit_sha": "",
        "git_task_branch_pushed": False,
        "git_main_status": "",
        "git_main_sha": "",
        "git_integration_status": "",
        "git_integration_reason": "",
        "coding_agent_result": "",
        "coding_agent_success": False,
        "coding_agent_changed_files": (),
        "restart_required": False,
        "coding_agent_retry_count": 0,
        "coding_agent_correction": "",
        "coding_agent_command": "",
        "coding_agent_returncode": None,
        "coding_agent_timed_out": False,
        "coding_agent_performed_by": "",
        "coding_agent_validation": "",
        "validation_command": "",
        "validation_command_source": "",
        "validation_command_retry_count": 0,
        "validation_command_exhausted": False,
        "diff_summary": "",
        "git_changed_files": [],
        "validation_passed": None,
        "validation_output_tail": "",
        "out_of_scope_paths": [],
        "review_status": "",
        "review_findings": [],
        "review_correction": "",
        "review_agent_performed_by": "",
        "review_correction_count": 0,
        "verification_status": "",
        "verification_reason": "",
        "verification_correction": "",
        "verification_attempt_count": 0,
    }



def _run_budget_tracker(
    state: GraphState, settings: Any | None
) -> RunBudgetTracker | None:
    max_calls = state.get("orchestrator_run_max_calls")
    max_tokens = state.get("orchestrator_run_max_tokens")
    max_cost_usd = state.get("orchestrator_run_max_cost_usd")
    usage = {
        "calls_used": int(state.get("orchestrator_calls_used", 0)),
        "tokens_in_used": int(state.get("orchestrator_tokens_in_used", 0)),
        "tokens_out_used": int(state.get("orchestrator_tokens_out_used", 0)),
        "cost_usd_used": float(state.get("orchestrator_cost_usd_used", 0.0)),
    }
    if (
        isinstance(max_calls, int)
        and max_calls > 0
        and isinstance(max_tokens, int)
        and max_tokens > 0
        and isinstance(max_cost_usd, (int, float))
        and float(max_cost_usd) > 0
    ):
        return RunBudgetTracker(
            max_calls=max_calls,
            max_tokens=max_tokens,
            max_cost_usd=float(max_cost_usd),
            **usage,
        )
    if settings is not None:
        return RunBudgetTracker.from_settings(settings, **usage)
    return None


def _run_budget_usage_update(tracker: RunBudgetTracker) -> dict[str, Any]:
    return {
        "orchestrator_calls_used": tracker.calls_used,
        "orchestrator_tokens_in_used": tracker.tokens_in_used,
        "orchestrator_tokens_out_used": tracker.tokens_out_used,
        "orchestrator_tokens_used": tracker.tokens_used,
        "orchestrator_cost_usd_used": tracker.cost_usd_used,
    }


def _run_budgeted_orchestrator_call(
    state: GraphState,
    settings: Any | None,
    *,
    resume_node: NodeName,
    operation: Callable[[], T],
) -> tuple[T | None, Exception | None, dict[str, Any]]:
    """Run one logical orchestrator operation and return its persisted usage delta.

    The low-level provider boundary counts every real request made inside the
    operation, so JSON retries, incomplete responses, plain-text calls and web
    search all use the same run budget.  Budget exhaustion is returned as graph
    state rather than escaping as an exception so the checkpoint can preserve
    the counters before an interactive pause/resume.
    """

    tracker = _run_budget_tracker(state, settings)
    if tracker is None:
        try:
            return operation(), None, {}
        except Exception as error:
            return None, error, {}

    try:
        with use_run_budget(tracker):
            value = operation()
    except RunBudgetExceededError as error:
        logger.warning("Run budget blocked %s: %s", resume_node.value, error)
        return (
            None,
            error,
            {
                **_run_budget_usage_update(tracker),
                "run_budget_exceeded": True,
                "run_budget_exceeded_reason": str(error),
                "run_budget_resume_node": resume_node.value,
                "run_budget_terminated": False,
            },
        )
    except Exception as error:
        return None, error, _run_budget_usage_update(tracker)

    return (
        value,
        None,
        {
            **_run_budget_usage_update(tracker),
            "run_budget_exceeded": False,
            "run_budget_exceeded_reason": "",
            "run_budget_resume_node": "",
            "run_budget_terminated": False,
        },
    )


def _budget_blocked(error: Exception | None) -> bool:
    return isinstance(error, RunBudgetExceededError)


def _route_to_run_budget_interrupt(state: GraphState) -> bool:
    if not state.get("run_budget_exceeded", False):
        return False
    logger.info(
        "Decision: run budget exceeded at %s -> RUN_BUDGET_INTERRUPT",
        state.get("run_budget_resume_node", "unknown"),
    )
    return True


def run_budget_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Fail closed for non-interactive runs or pause Telegram until the operator retries."""

    _log_node_start("budget", "RUN_BUDGET_INTERRUPT", "Handle orchestrator run budget")
    reason = str(state.get("run_budget_exceeded_reason", "")).strip() or "Run budget reached."
    resume_node = str(state.get("run_budget_resume_node", "")).strip()
    allowed_resume_nodes = {
        NodeName.READ_REQUEST.value,
        NodeName.CHECK_CODE_LOOK_NEED.value,
        NodeName.CHECK_PROJECT_GUIDANCE.value,
        NodeName.TECH_LEAD_ANALYSE.value,
        NodeName.CHECK_RESEARCH.value,
        NodeName.DISCOVER_RESEARCH_SOURCE.value,
        NodeName.REVIEW_RISK.value,
        NodeName.REVIEW_PLAN.value,
        NodeName.APPROVAL_INTERRUPT.value,
        NodeName.VERIFY_COMPLETION.value,
    }
    if resume_node not in allowed_resume_nodes:
        logger.error("Run budget has invalid resume node %r; failing closed.", resume_node)
        return {
            "run_budget_terminated": True,
            "run_budget_exceeded_reason": (
                f"{reason} Invalid resume target: {resume_node or '<empty>'}."
            ),
        }

    if not state.get("run_budget_interactive", False):
        logger.warning("Run budget reached in non-interactive workflow; failing closed: %s", reason)
        return {"run_budget_terminated": True}

    settings = load_settings()
    result = interrupt(
        {
            "kind": "run_budget",
            "reason": reason,
            "calls_used": int(state.get("orchestrator_calls_used", 0)),
            "tokens_used": int(state.get("orchestrator_tokens_used", 0)),
            "cost_usd_used": float(state.get("orchestrator_cost_usd_used", 0.0)),
            "max_calls": settings.orchestrator_run_max_calls,
            "max_tokens": settings.orchestrator_run_max_tokens,
            "max_cost_usd": settings.orchestrator_run_max_cost_usd,
        }
    )
    action = str(result.get("action", "")).strip().lower() if isinstance(result, dict) else ""
    if action != "retry":
        logger.info("Run budget interrupt resumed without retry; stopping task.")
        return {"run_budget_terminated": True}

    logger.info("Run budget interrupt resumed: retrying blocked step %s", resume_node)
    return {
        "run_budget_exceeded": False,
        "run_budget_terminated": False,
        "orchestrator_run_max_calls": settings.orchestrator_run_max_calls,
        "orchestrator_run_max_tokens": settings.orchestrator_run_max_tokens,
        "orchestrator_run_max_cost_usd": settings.orchestrator_run_max_cost_usd,
    }


def route_after_run_budget_interrupt(state: GraphState) -> str:
    if state.get("run_budget_terminated", False):
        return "end"
    return str(state.get("run_budget_resume_node", ""))


def read_and_classify_request_node(state: GraphState) -> dict[str, Any]:
    """Validate the request, then check it's actually AI Tech Lead's job.

    Merges what used to be two separate deterministic nodes plus a new
    relevance check, since neither original split point ever branched.
    """

    _log_node_start("1", "READ_AND_CLASSIFY_REQUEST", "Read and classify request")

    request = state["request"].strip()
    if not request:
        raise ValueError("Request cannot be empty.")
    logger.info("Task: %s", _request_title(request))

    decision, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        None,
        resume_node=NodeName.READ_REQUEST,
        operation=lambda: classify_request_relevance(request),
    )
    if _budget_blocked(error):
        return budget_updates
    if error is not None:
        raise error
    assert decision is not None
    logger.info("ATL relevance: %s (%s)", decision.is_atl_relevant, decision.reason)

    return {
        "request": request,
        "restart_required": False,
        "atl_relevant": decision.is_atl_relevant,
        "atl_relevance_reason": decision.reason,
        **budget_updates,
    }


def route_after_read_and_classify_request(state: GraphState) -> str:
    """Route to the normal path, or end early if this isn't ATL's job at all."""

    _log_decision_start("1", "ROUTE_AFTER_READ_AND_CLASSIFY", "Route after request classification")
    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if not state.get("atl_relevant", True):
        logger.info("Decision: not relevant -> END_NODE")
        return "not relevant"
    logger.info("Decision: relevant -> PROJECT_SCOPE_DECISION")
    return "relevant"


def project_scope_decision_node(state: GraphState) -> dict[str, Any]:
    """Decide new vs existing project. Stub: always "existing" for now.

    New-project handling is a separate, not-yet-built capability (backlog
    ATL-081). This node exists so that future work has a clear place to
    plug in the "new project" branch without re-wiring the graph.
    """

    _log_node_start("1a", "PROJECT_SCOPE_DECISION", "Decide project scope")
    return {"project_scope": "existing"}


def _fetch_known_backlog_item(
    context: TargetProjectContext, item_id: str, request_id: str
) -> TargetProjectContext:
    """Fetch one item from the project's already-known backlog resource.

    Reuses the same SheetsBacklogRepository/layout construction as backlog
    refinement (`_backlog_refinement_repository` in agent_task_runner.py) —
    the resource is already fully identified by `backlog_project`, so there is
    nothing project- or provider-specific here, and no prefix is assumed.
    Raises ValueError/BacklogValidationError (row not found / not unique) or
    BacklogSourceUnavailableError (sheet unreachable); the caller decides how
    to report that clearly and stop.

    Snapshots the fetched row the same way the pre-graph explicit
    `backlog_reference` path does (agent_task_runner.py), and stores the row
    hash on the returned `BacklogItemContext` so completion sync
    (`_sync_backlog_completion`) can use it later as the optimistic-
    concurrency baseline — one sync mechanism, fed by either fetch path.
    """

    backlog_project = context.backlog_project
    assert backlog_project is not None  # narrowed by the caller before invoking
    settings = load_settings()
    repository = repository_for(
        backlog_project.spreadsheet_id,
        backlog_project.sheet_name,
        credentials_path=settings.backlog_google_credentials_path,
        layout=BacklogSheetLayout(columns=backlog_project.columns),
    )
    source_record = repository.get_item_with_source(item_id)
    if request_id:
        BacklogRuntimeStore().save_snapshot(
            request_id=request_id,
            project_key=backlog_project.project_key,
            spreadsheet_id=backlog_project.spreadsheet_id,
            sheet_name=backlog_project.sheet_name,
            item_id=item_id,
            row_data=source_record.row_values,
            row_hash=source_record.row_hash,
            fetched_at=source_record.fetched_at,
        )
    else:
        logger.warning(
            "Backlog item '%s' fetched with no request_id in graph state — "
            "skipping snapshot; completion sync for this item will not have "
            "an audit-trail row (conflict detection still works).",
            item_id,
        )
    backlog_item = BacklogItemContext(
        project_key=backlog_project.project_key,
        spreadsheet_id=backlog_project.spreadsheet_id,
        sheet_name=backlog_project.sheet_name,
        item_id=item_id,
        title=source_record.item.title,
        body=source_record.item.body,
        row_hash=source_record.row_hash,
        fetched_at=source_record.fetched_at,
        priority=source_record.item.priority,
        size=source_record.item.complexity,
    )
    return replace(context, backlog_item=backlog_item)


def _discover_backlog_project(project_key: str) -> BacklogProjectContext | None:
    """Bounded backlog-location discovery for a known project.

    Checks exactly one authoritative, already-loaded local source —
    ``settings.backlog_projects[project_key]`` — the same registry
    ``resolve_backlog_reference`` already trusts for its own project_key path
    (backlog_reference.py). Returns None (never raises) when the project_key
    isn't registered there, or is registered without both a spreadsheet_id and
    a sheet_name: "not verified" and "not resolvable" are the same outcome
    here, since a partial entry can't be trusted either.

    Deliberately does not: infer or guess an item ID prefix, look at the
    filesystem or any other repository, call any network service, or write
    anything back to Hub or elsewhere. Discovery only reads local settings
    already resident in memory.
    """

    settings = load_settings()
    entry = (settings.backlog_projects or {}).get(project_key)
    if entry is None:
        return None
    spreadsheet_id = str(entry.get("spreadsheet_id", "")).strip()
    sheet_name = str(entry.get("sheet_name", "")).strip()
    if not spreadsheet_id or not sheet_name:
        return None
    return BacklogProjectContext(
        project_key=project_key,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
    )


def resolve_context_node(state: GraphState) -> dict[str, Any]:
    """Resolve references from explicit supplied context; never infer prefix meaning."""

    _log_node_start("1b", "RESOLVE_CONTEXT", "Resolve project and resource context")
    context = _target_project_context_from_state(state)
    result = resolve_request_context(
        state["request"],
        target_project_context=context,
        clarification_answer=state.get("context_clarification_answer", ""),
    )

    # The project is known but its backlog resource isn't: try bounded discovery
    # before falling back to asking a human. If discovery finds a verified
    # location, re-resolve with it attached so the existing known-backlog fetch
    # path below runs exactly as it would if the caller had supplied it.
    if (
        context is not None
        and context.backlog_project is None
        and context.project_key
        and result.get("unresolved_references")
    ):
        discovered_backlog_project = _discover_backlog_project(context.project_key)
        if discovered_backlog_project is not None:
            logger.info(
                "Backlog location discovered for project_key=%s: spreadsheet=%s sheet=%s",
                context.project_key,
                discovered_backlog_project.spreadsheet_id,
                discovered_backlog_project.sheet_name,
            )
            context = replace(context, backlog_project=discovered_backlog_project)
            result = resolve_request_context(
                state["request"],
                target_project_context=context,
                clarification_answer=state.get("context_clarification_answer", ""),
            )

    fetch_candidate = str(result.pop("backlog_fetch_candidate", "")).strip()
    if fetch_candidate and context is not None and context.backlog_project is not None:
        try:
            context = _fetch_known_backlog_item(
                context, fetch_candidate, state.get("request_id", "")
            )
        except (ValueError, BacklogValidationError, BacklogSourceUnavailableError) as exc:
            logger.info(
                "Backlog item '%s' could not be fetched from the known backlog "
                "resource: %s",
                fetch_candidate,
                exc,
            )
            return {
                "backlog_item_not_found_reason": (
                    f"Backlog item '{fetch_candidate}' could not be retrieved from "
                    f"the known backlog: {exc}"
                ),
                "orchestrator_input_required": False,
            }
        result = resolve_request_context(
            state["request"],
            target_project_context=context,
            clarification_answer=state.get("context_clarification_answer", ""),
            allow_backlog_fetch=False,
        )
        result.pop("backlog_fetch_candidate", None)
        result["target_project_context"] = context.to_payload()

    question = str(result.pop("clarification_question", "")).strip()
    if question:
        result.update({
            "orchestrator_input_required": True,
            "orchestrator_input_reason": (
                "A referenced project resource could not be resolved safely."
            ),
            "orchestrator_input_question": question,
        })
    else:
        result["orchestrator_input_required"] = False
    return result


def route_after_resolve_context(state: GraphState) -> str:
    """Route according to whether the requested project context was resolved safely."""

    _log_decision_start(
        "1b",
        "ROUTE_AFTER_RESOLVE_CONTEXT",
        "Route after project context resolution",
    )
    if state.get("backlog_item_not_found_reason"):
        logger.info(
            "Decision: known backlog item could not be fetched -> END_NODE (%s)",
            state.get("backlog_item_not_found_reason"),
        )
        return "backlog item not found"
    if state.get("orchestrator_input_required"):
        retry_count = int(state.get("context_clarification_retry_count", 0))
        if retry_count >= CONTEXT_CLARIFICATION_MAX_RETRIES:
            logger.info(
                "Decision: clarification limit reached (%d/%d) -> END_NODE",
                retry_count,
                CONTEXT_CLARIFICATION_MAX_RETRIES,
            )
            return "clarification limit reached"
        logger.info(
            "Decision: clarification needed (%d/%d used) -> CONTEXT_CLARIFICATION_INTERRUPT",
            retry_count,
            CONTEXT_CLARIFICATION_MAX_RETRIES,
        )
        return "clarification needed"
    logger.info("Decision: context found -> CHECK_CODE_LOOK_NEED")
    return "context found"


def context_clarification_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause and ask the human to clarify one unresolved project/resource reference.

    Resumes by re-running context resolution (loops back to RESOLVE_CONTEXT) with
    the human's answer folded in. Bounded to CONTEXT_CLARIFICATION_MAX_RETRIES
    rounds by route_after_resolve_context — this node does not enforce the limit
    itself, it only asks and records the answer.
    """

    _log_node_start(
        "1b", "CONTEXT_CLARIFICATION_INTERRUPT", "Pause for human context clarification"
    )
    question = str(state.get("orchestrator_input_question", "")).strip()
    reason = str(state.get("orchestrator_input_reason", "")).strip()
    retry_count = int(state.get("context_clarification_retry_count", 0))
    logger.info(
        "Context clarification interrupt: retry_count=%d question=%s",
        retry_count,
        question[:120],
    )
    result = interrupt(
        {
            "kind": "context_clarification",
            "question": question,
            "reason": reason,
            "retry_count": retry_count,
        }
    )
    answer = str(result) if not isinstance(result, dict) else str(result.get("text", result))
    logger.info(
        "Context clarification interrupt resumed: answer_length=%d preview=%s",
        len(answer),
        answer[:120],
    )
    return {
        "context_clarification_answer": answer,
        "context_clarification_retry_count": retry_count + 1,
        "orchestrator_input_required": False,
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
    }


def check_research_node(state: GraphState) -> dict[str, Any]:
    """Identify a knowledge gap, if any, and check research evidence requirements."""

    _log_node_start("2a", "CHECK_RESEARCH", "Check research evidence requirements")
    settings = load_settings()
    context = _target_project_context_from_state(state)
    code_context_root = (
        context.project_root.strip()
        if context is not None and context.project_root.strip()
        else str(state.get("resolved_project_root", "")).strip() or None
    )
    if code_context_root:
        logger.info(
            "[LEARN] Code context will scan resolved target project root: %s",
            code_context_root,
        )

    research_request = (
        state.get("formulated_task", "")
        or state.get("bounded_request", "")
        or state["request"]
    )
    result, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.CHECK_RESEARCH,
        operation=lambda: check_research_requirements(
            research_request, settings, code_context_root=code_context_root
        ),
    )
    if _budget_blocked(error):
        return budget_updates
    if error is not None:
        raise error
    assert result is not None

    partial: dict[str, Any] = {
        "research_checked": True,
        "research_evidence_required": result.has_gap,
        "research_gap_question": result.gap_question,
        "research_source_titles": list(result.usable_source_titles),
        "research_source_locations": list(result.usable_source_locations),
        "research_source_summaries": list(result.usable_source_summaries),
        "online_research_approved": not result.online_research_needed,
        **budget_updates,
    }

    if result.online_research_needed:
        domains_text = (
            ", ".join(settings.research_allowed_domains)
            if settings.research_allowed_domains
            else "no domains configured"
        )
        question = (
            f"Local docs found {result.sources_found} relevant source(s) "
            f"(minimum {settings.research_min_local_sources} required) for this knowledge gap:\n"
            f'"{result.gap_question}"\n'
            f"Reason: {result.gap_reason}\n"
            f"Do you want me to fetch from the approved online source registry next "
            f"(bounded to: {domains_text})?"
        )
        partial["orchestrator_input_required"] = True
        partial["orchestrator_input_reason"] = result.gap_reason
        partial["orchestrator_input_question"] = question
        logger.info(
            "[LEARN] Research interrupt required: sources_found=%d gap_question=%s reason=%s",
            result.sources_found,
            result.gap_question,
            result.gap_reason,
        )
    else:
        if result.has_gap:
            logger.info(
                "[LEARN] Research evidence satisfied: %d local source(s) available: %s",
                result.sources_found,
                ", ".join(result.usable_source_titles),
            )
            logger.info(
                "[LEARN] Local research source locations: %s",
                ", ".join(result.usable_source_locations),
            )

    return partial


def discover_research_source_node(state: GraphState) -> dict[str, Any]:
    """Try to discover a candidate official source for the identified gap.

    Only reached because check_research_node already decided online research
    is needed. This node never itself decides to skip the human approval
    gate — it only changes what that gate asks about. Falls back to
    check_research_node's existing bounded-registry question when discovery
    is off, errors, or finds no safe candidate.
    """

    _log_node_start("2a1", "DISCOVER_RESEARCH_SOURCE", "Discover a candidate official source")
    settings = load_settings()
    gap_question = str(state.get("research_gap_question", "")).strip()

    context = _target_project_context_from_state(state)
    target_root = (
        context.project_root if context and context.project_root else settings.project_root
    )
    policy = select_research_policy(gap_question, settings, project_root=target_root)
    policy_state = {
        "research_policy_profiles": list(policy.profile_names),
        "research_trusted_domains": list(policy.trusted_domains),
        "research_seed_urls": list(policy.seed_urls),
    }
    logger.info(
        "[LEARN] Research source policy: profiles=%s domains=%s seeds=%d",
        ",".join(policy.profile_names) or "configured-fallback",
        ",".join(policy.trusted_domains),
        len(policy.seed_urls),
    )

    candidate, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.DISCOVER_RESEARCH_SOURCE,
        operation=lambda: discover_official_source(
            gap_question, settings, trusted_domains=policy.trusted_domains
        ),
    )
    if _budget_blocked(error):
        return {**policy_state, **budget_updates}
    if error is not None:
        raise error
    if candidate is None:
        logger.info("[LEARN] No discovered source candidate; keeping bounded-registry question.")
        return {**policy_state, **budget_updates}

    question = (
        "I found a candidate official documentation page for this knowledge gap:\n"
        f'"{gap_question}"\n'
        f"{candidate.title} — {candidate.url}\n"
        "Do you approve fetching this specific URL? No other URL will be fetched "
        "without separate approval."
    )
    logger.info("[LEARN] Discovered source candidate for approval: %s", candidate.url)
    return {
        **policy_state,
        "discovered_source_url": candidate.url,
        "discovered_source_title": candidate.title,
        "orchestrator_input_question": question,
        **budget_updates,
    }


def route_after_discover_research_source(state: GraphState) -> str:
    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    return "research interrupt"


def research_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause and ask for human approval before running online research."""

    _log_node_start("2a", "RESEARCH_INTERRUPT", "Pause for human online research approval")
    question = str(state.get("orchestrator_input_question", "")).strip()
    logger.info("Research interrupt: awaiting human approval. Question: %s", question)
    result = interrupt({"kind": "research_approval", "question": question})
    approved = bool(result.get("approved", False)) if isinstance(result, dict) else bool(result)
    logger.info("Research interrupt resumed: approved=%s", approved)
    return {
        "online_research_approved": approved,
        "orchestrator_input_required": False,
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
    }


def route_after_check_research(state: GraphState) -> str:
    """Route after research check: interrupt if complex task lacks local sources, else continue."""

    _log_decision_start("2a", "ROUTE_AFTER_CHECK_RESEARCH", "Route after research evidence check")

    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if state.get("research_evidence_required") and not state.get("online_research_approved", True):
        logger.info("Decision: research interrupt needed -> DISCOVER_RESEARCH_SOURCE")
        return "research needed"

    logger.info("Decision: no research needed -> TECH_LEAD_ANALYSE")
    return "no research needed"


def route_after_research_interrupt(state: GraphState) -> str:
    """Route after the human answers the research approval question."""

    _log_decision_start(
        "2a",
        "ROUTE_AFTER_RESEARCH_INTERRUPT",
        "Route after research approval",
    )

    if state.get("online_research_approved", False):
        logger.info("Decision: research approved -> COLLECT_RESEARCH_EVIDENCE")
        return "approved"

    logger.info("Decision: research rejected -> END_NODE")
    return "declined"


def collect_research_evidence_node(state: GraphState) -> dict[str, Any]:
    """Fetch bounded official docs after the human approves online research."""

    _log_node_start("2b", "COLLECT_RESEARCH_EVIDENCE", "Fetch approved online documentation")
    settings = load_settings()

    local_titles = list(state.get("research_source_titles", []))
    local_locations = list(state.get("research_source_locations", []))
    local_summaries = list(state.get("research_source_summaries", []))
    logger.info(
        "[LEARN] Research evidence before online fetch: local_sources=%d approved=%s",
        len(local_titles),
        state.get("online_research_approved", False),
    )

    gap_question = (
        state.get("research_gap_question", "")
        or state.get("bounded_request", "")
        or state["request"]
    )
    discovered_url = str(state.get("discovered_source_url", "")).strip()
    extra_urls = [discovered_url] if discovered_url else None
    online_sources = collect_online_research_sources(
        gap_question,
        settings,
        extra_urls=extra_urls,
        seed_urls=list(state.get("research_seed_urls", [])) or None,
        trusted_domains=list(state.get("research_trusted_domains", [])) or None,
    )
    online_titles = [source.title for source in online_sources]
    online_locations = [source.location for source in online_sources]
    online_summaries = [source.summary for source in online_sources]

    newly_cached = sum(
        1
        for source in online_sources
        if save_online_source_to_cache(
            title=source.title,
            location=source.location,
            summary=source.summary,
            excerpt=source.excerpt,
            question=gap_question,
            project_root=Path(settings.project_root),
        )
    )
    logger.info("[LEARN] Online sources saved to local cache: %d new", newly_cached)
    logger.info(
        "[LEARN] Research handoff summary: local_sources=%d online_sources=%d "
        "new_cache_notes=%d reused_cache_sources=%d",
        len(local_titles),
        len(online_sources),
        newly_cached,
        len(online_sources) - newly_cached,
    )

    logger.info(
        "[LEARN] Online research fetch result: fetched=%d configured_limit=%d",
        len(online_sources),
        settings.research_max_online_source_urls,
    )
    if online_sources:
        logger.info(
            "[LEARN] Online research sources: %s",
            ", ".join(source.title for source in online_sources),
        )
    else:
        logger.warning(
            "[LEARN] Online research returned no usable sources; continuing with local evidence."
        )

    combined_titles = local_titles + online_titles
    combined_locations = local_locations + online_locations
    combined_summaries = local_summaries + online_summaries
    logger.info("[LEARN] Total research sources available for handoff: %d", len(combined_titles))

    return {
        "research_source_titles": combined_titles,
        "research_source_locations": combined_locations,
        "research_source_summaries": combined_summaries,
    }


def review_risk_node(state: GraphState) -> dict[str, Any]:
    """Decide whether this task needs human approval.

    The graph owns this decision now. The backlog may describe the task, but it
    no longer decides whether approval is required.
    """

    _log_node_start("3", "REVIEW_RISK", "Review task risk")
    settings = load_settings()
    logger.info("AI channel: OpenAI Responses API")
    logger.info("AI model: %s", settings.orchestrator_ai_model)
    logger.info("AI enabled: %s", settings.orchestrator_ai_enabled)

    # Prefer the tech lead's formulated task + technical direction over the raw
    # request: risk now reflects the actual implementation scope decided by
    # TECH_LEAD_ANALYSE (which runs before this node), not just the original ask.
    formulated_task = str(state.get("formulated_task", "")).strip()
    brief = str(state.get("brief", "")).strip()
    if formulated_task:
        risk_review_input = formulated_task
        if brief:
            risk_review_input = f"{formulated_task}\n\nTechnical direction:\n{brief}"
    else:
        risk_review_input = state.get("bounded_request", "") or state["request"]

    decision, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.REVIEW_RISK,
        operation=lambda: review_task_risk(risk_review_input),
    )
    if _budget_blocked(error):
        return budget_updates
    if error is not None:
        raise error
    assert decision is not None
    needs_approval = decision.needs_approval
    approval_reason = decision.approval_reason
    risk_level = decision.risk_level

    logger.info("Risk level: %s", risk_level)

    if state.get("force_approval"):
        needs_approval = True
        forced_note = "Interrupt Before Implementation flag set in backlog item."
        approval_reason = f"{forced_note} {approval_reason}".strip()
        logger.info(
            "[LEARN] Force approval: Interrupt Before Implementation flag is set "
            "— overriding to needs_approval=True."
        )
    elif settings.sleep_mode and risk_level in ("LOW", "MEDIUM"):
        needs_approval = False
        logger.info(
            "[SLEEP] Sleep mode active: risk_level=%s — proceeding without approval.", risk_level
        )
    elif settings.sleep_mode and risk_level == "UNKNOWN":
        needs_approval = True
        logger.info("[SLEEP] Sleep mode active: risk_level=UNKNOWN — pausing for safety.")
    elif settings.sleep_mode:
        logger.info(
            "[SLEEP] Sleep mode active: risk_level=%s — approval required.", risk_level
        )

    logger.info(
        "Approval: %s (%s)",
        "required" if needs_approval else "not required",
        approval_reason,
    )
    return {
        "needs_approval": needs_approval,
        "approval_reason": approval_reason,
        "risk_level": risk_level,
        **budget_updates,
    }


def check_code_look_need_node(state: GraphState) -> dict[str, Any]:
    """Cheap check: does this task need Codex to look at real code first?

    Scales effort to complexity — most tasks skip this. Fails open toward
    skipping if AI is disabled or the check errors, since this only saves
    cost/time and is never the thing standing between a task and safety.
    """

    _log_node_start("1c", "CHECK_CODE_LOOK_NEED", "Check if code look is needed")
    request_text = state.get("bounded_request", "") or state["request"]
    decision, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        None,
        resume_node=NodeName.CHECK_CODE_LOOK_NEED,
        operation=lambda: check_code_look_need(request_text),
    )
    if _budget_blocked(error):
        return budget_updates
    if error is not None:
        raise error
    assert decision is not None
    logger.info("Code look needed: %s (%s)", decision.needs_code_look, decision.reason)
    return {
        "code_look_needed": decision.needs_code_look,
        "code_look_need_reason": decision.reason,
        **budget_updates,
    }


def route_after_check_code_look_need(state: GraphState) -> str:
    """Route to Codex's read-only look, or straight to analysis if not needed."""

    _log_decision_start("1c", "ROUTE_AFTER_CHECK_CODE_LOOK_NEED", "Route after code-look decision")
    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if state.get("code_look_needed"):
        logger.info("Decision: code look needed -> CODEX_READS_CODE")
        return "needed"
    logger.info("Decision: code look not needed -> CHECK_PROJECT_GUIDANCE")
    return "not needed"


def codex_reads_code_node(
    state: GraphState,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Codex looks at real code, read-only, and reports back what it found.

    Reuses the same run_coding_agent machinery as REQUEST_PLAN, explicitly
    forced to read-only — this is a look, not implementation. If Codex is
    stuck or uncertain it's asked to prefix its report with "NEEDS_HELP:"
    so that signal carries into ATL's own analysis and, downstream, risk
    review rather than being silently lost.
    """

    _log_node_start("1c1", "CODEX_READS_CODE", "Codex reads code, read-only")
    settings = load_settings()

    instruction = render_prompt(
        CODE_RECON_INSTRUCTION_PROMPT_KEY,
        request=state.get("bounded_request", "") or state["request"],
    )
    target_project_root = _target_project_root(state, settings)
    result = run_coding_agent(
        agent_instruction=instruction,
        project_root=target_project_root,
        settings=settings,
        sandbox_override="read-only",
    )
    report = result.stdout.strip()
    logger.info(
        "Code recon report (%d chars): %s",
        len(report),
        _single_line_preview(report, limit=360),
    )
    if report.startswith("NEEDS_HELP:"):
        logger.warning("Codex flagged it needs help during code recon: %s", report[:200])
    if result.changed_files_delta:
        logger.warning("Code recon unexpectedly changed files: %s", result.changed_files_delta)
    if progress_callback is not None:
        progress_callback("Codex finished looking at the code.")

    return {"code_recon_report": report}


_GUIDANCE_NOTE_PATH_PATTERN = re.compile(r"^([^\s:(]+\.md)")


def _guidance_paths_and_hash(notes: list[str]) -> tuple[list[str], str]:
    """Derive the file paths behind the selected notes and one compact content
    hash — metadata extracted from what discover_project_guidance already
    returned, not a new discovery pass or a new persistence store. The
    completed run writes these bounded paths and hashes into ATL-038's compact
    audit receipt; resumable state remains in the LangGraph checkpoint.
    """

    paths: list[str] = []
    for note in notes:
        match = _GUIDANCE_NOTE_PATH_PATTERN.match(note)
        if match and match.group(1) not in paths:
            paths.append(match.group(1))
    combined_hash = hashlib.sha256("\x1f".join(notes).encode("utf-8")).hexdigest() if notes else ""
    return paths, combined_hash


def check_project_guidance_node(state: GraphState) -> dict[str, Any]:
    """Bounded discovery, then a governed check for missing/conflicting guidance.

    Discovery itself (project_guidance_discovery.py) is cheap and never blocks.
    The governance check (project_guidance_governance.py) decides whether what
    was found — or its absence — actually matters enough to pause and ask a
    human before this run uses anything beyond what was safely verified.
    """

    _log_node_start("1d", "CHECK_PROJECT_GUIDANCE", "Check project guidance coverage")
    settings = load_settings()

    if not settings.project_guidance_discovery_enabled:
        return {
            "project_guidance_notes": [],
            "project_guidance_status": "",
            "project_guidance_requires_review": False,
            "project_guidance_paths": [],
            "project_guidance_hash": "",
            "project_guidance_changed_on_resume": False,
        }

    request_text = state.get("bounded_request", "") or state["request"]
    notes = discover_project_guidance(request_text, _soft_target_project_root(state, settings))
    decision, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.CHECK_PROJECT_GUIDANCE,
        operation=lambda: review_project_guidance(request_text, list(notes), settings),
    )
    if _budget_blocked(error):
        return budget_updates
    if error is not None:
        raise error
    assert decision is not None
    paths, guidance_hash = _guidance_paths_and_hash(list(notes))

    logger.info(
        "[LEARN] Project guidance governance: status=%s requires_review=%s",
        decision.status,
        decision.requires_review,
    )
    logger.info(
        "[LEARN] Project guidance selected: paths=%s hash=%s", paths, guidance_hash[:12]
    )

    return {
        "project_guidance_notes": list(notes),
        "project_guidance_status": decision.status,
        "project_guidance_requires_review": decision.requires_review,
        "project_guidance_summary": decision.summary,
        "project_guidance_related_locations": decision.related_locations,
        "project_guidance_proposed_change": decision.proposed_change,
        "project_guidance_reason": decision.reason,
        "project_guidance_paths": paths,
        "project_guidance_hash": guidance_hash,
        "project_guidance_changed_on_resume": False,
        **budget_updates,
    }


def route_after_check_project_guidance(state: GraphState) -> str:
    """Route to the governance interrupt only when review is actually required."""

    _log_decision_start(
        "1d", "ROUTE_AFTER_CHECK_PROJECT_GUIDANCE", "Route after project guidance check"
    )
    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if state.get("project_guidance_requires_review"):
        logger.info(
            "Decision: guidance status=%s requires review -> PROJECT_GUIDANCE_INTERRUPT",
            state.get("project_guidance_status"),
        )
        return "review needed"
    logger.info("Decision: guidance sufficient or not required -> TECH_LEAD_ANALYSE")
    return "no review needed"


def project_guidance_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause for explicit human approval before using a missing/conflicting-guidance proposal.

    Approval only permits using the proposed content as this run's context —
    it never writes anything back to the target project's own files. Rejecting
    a proposal that was flagged as required for safe/correct work ends the run
    clearly (mirrors RESEARCH_INTERRUPT's reject -> END_NODE) rather than
    silently proceeding on an admitted gap or unresolved conflict.
    """

    _log_node_start(
        "1d1", "PROJECT_GUIDANCE_INTERRUPT", "Pause for project guidance governance approval"
    )
    status = str(state.get("project_guidance_status", "")).strip()
    summary = str(state.get("project_guidance_summary", "")).strip()
    related_locations = list(state.get("project_guidance_related_locations", []))
    proposed_change = str(state.get("project_guidance_proposed_change", "")).strip()
    reason = str(state.get("project_guidance_reason", "")).strip()

    logger.info("Project guidance governance interrupt: status=%s summary=%s", status, summary)
    result = interrupt(
        {
            "kind": "project_guidance_governance",
            "status": status,
            "summary": summary,
            "related_locations": related_locations,
            "proposed_change": proposed_change,
            "reason": reason,
        }
    )
    approved = bool(result.get("approved", False)) if isinstance(result, dict) else bool(result)
    logger.info("Project guidance governance interrupt resumed: approved=%s", approved)

    if approved:
        notes = list(state.get("project_guidance_notes", []))
        if proposed_change:
            notes.append(f"(human-approved this run only, not committed to the project) {proposed_change}")
        return {
            "project_guidance_notes": notes,
            "project_guidance_requires_review": False,
            "project_guidance_rejected_summary": "",
        }

    return {
        "project_guidance_requires_review": False,
        "project_guidance_rejected_summary": (
            f"Project guidance {status or 'issue'} and the proposed change was not "
            f"approved: {summary}"
        ),
    }


def route_after_project_guidance_interrupt(state: GraphState) -> str:
    """Route after the human approves or rejects the governance proposal."""

    _log_decision_start(
        "1d1",
        "ROUTE_AFTER_PROJECT_GUIDANCE_INTERRUPT",
        "Route after project guidance governance approval",
    )
    if state.get("project_guidance_rejected_summary"):
        logger.info("Decision: guidance proposal rejected -> END_NODE")
        return "rejected"
    logger.info("Decision: guidance proposal approved -> TECH_LEAD_ANALYSE")
    return "approved"


def tech_lead_analyse_node(state: GraphState) -> dict[str, Any]:
    """Tech lead analysis: formulate the task and produce high-level technical direction."""

    _log_node_start(
        "2", "TECH_LEAD_ANALYSE", "Tech lead analysis — formulate task and set direction"
    )
    settings = load_settings()

    research_evidence = _format_research_evidence(
        state.get("research_source_titles", []),
        state.get("research_source_locations", []),
        state.get("research_source_summaries", []),
    )

    # Discovery and governance already ran in CHECK_PROJECT_GUIDANCE, once,
    # before this node's first entry — reuse its result rather than
    # re-running it on every later pass (research loop, approval revision).
    project_guidance = list(state.get("project_guidance_notes", []))

    analysis, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.TECH_LEAD_ANALYSE,
        operation=lambda: analyse_task(
            request=state.get("bounded_request", "") or state["request"],
            task_feedback=list(state.get("task_feedback", [])),
            approval_reason=state.get("approval_reason", ""),
            research_evidence=research_evidence,
            settings=settings,
            code_recon_report=state.get("code_recon_report", ""),
            project_guidance=project_guidance,
        ),
    )
    if _budget_blocked(error):
        return budget_updates
    if error is not None:
        raise error
    assert analysis is not None

    logger.info(
        "Task statement (%d chars): %s",
        len(analysis.task_statement),
        _single_line_preview(analysis.task_statement),
    )
    logger.info(
        "Tech direction (%d chars): %s",
        len(analysis.tech_direction),
        _single_line_preview(analysis.tech_direction),
    )
    return {
        "formulated_task": analysis.task_statement,
        "brief": analysis.tech_direction,
        **budget_updates,
    }


def route_after_tech_lead_analyse(state: GraphState) -> str:
    """Route after tech lead analysis: check research once, then always review risk.

    Tech Lead Analysis runs before the research gap check so the knowledge gap
    (if any) is judged against the orchestrator's own formulated task, not the
    raw request. ``research_checked`` is set by CHECK_RESEARCH and is what lets
    this same node be re-entered later (e.g. after research evidence arrives,
    or on an approval "request changes" revision) without re-running the
    research gap check a second time.
    """

    _log_decision_start("2", "ROUTE_AFTER_TECH_LEAD_ANALYSE", "Route after tech lead analysis")

    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if not state.get("research_checked"):
        logger.info("Decision: research not yet checked -> CHECK_RESEARCH")
        return "check research"

    logger.info("Decision: research already checked -> REVIEW_RISK")
    return "analysis complete"


def route_after_review_risk(state: GraphState) -> str:
    """Route after risk review: approval interrupt if risky, otherwise plan request.

    Risk is now assessed against the formulated task and tech direction
    (state produced by TECH_LEAD_ANALYSE), not the raw request, since the
    exact implementation scope is only known once analysis has run.
    """

    _log_decision_start("3", "ROUTE_AFTER_REVIEW_RISK", "Route after risk review")

    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if state.get("approved"):
        logger.info("Decision: already approved -> REQUEST_PLAN")
        return "already approved"

    if state["needs_approval"]:
        logger.info("Decision: needs_approval=True -> APPROVAL_INTERRUPT")
        return "approval required"

    logger.info("Decision: no approval needed -> REQUEST_PLAN")
    return "low risk"


def route_after_approval(state: GraphState) -> str:
    """Choose the next LangGraph node name after a human approval decision."""

    _log_decision_start(
        "approval", "ROUTE_AFTER_APPROVAL", "Choose next graph path after human decision"
    )

    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    action = state.get("approval_action", "approve")

    if action == "cancel":
        logger.info("Decision: action=cancel -> END_NODE")
        return "cancelled"

    if action == "ask_question":
        logger.info("Decision: action=ask_question -> APPROVAL_INTERRUPT")
        return "question asked"

    if action == "request_changes":
        revision_count = int(state.get("approval_revision_count", 0))
        if revision_count >= APPROVAL_MAX_REVISION_CYCLES:
            logger.info(
                "Decision: revision limit reached (%d/%d) -> END_NODE",
                revision_count,
                APPROVAL_MAX_REVISION_CYCLES,
            )
            return "revision limit reached"
        logger.info(
            "Decision: action=request_changes (%d/%d) -> TECH_LEAD_ANALYSE",
            revision_count,
            APPROVAL_MAX_REVISION_CYCLES,
        )
        return "changes requested"

    if state.get("project_guidance_changed_on_resume"):
        logger.info(
            "Decision: action=approve but selected project guidance changed since "
            "checkpointing -> CHECK_PROJECT_GUIDANCE (re-planning required)"
        )
        return "guidance changed"

    logger.info("Decision: action=approve -> REQUEST_PLAN")
    return "approved"



def _target_project_root(state: GraphState, settings: Any) -> Path:
    """Return the workflow's resolved target root, falling back only for local self-runs.

    ``resolved_project_root`` is produced by the context-resolution step and, for
    subprocess callers, originates from the allowlist-validated project root.
    Project-aware nodes must use this helper instead of independently reading
    ``settings.project_root``.
    """

    context = _target_project_context_from_state(state)
    if context is not None and context.has_explicit_context:
        return context.require_project_root("target-project planning and execution")

    resolved = str(state.get("resolved_project_root", "")).strip()
    return Path(resolved or settings.project_root).resolve()


def _soft_target_project_root(state: GraphState, settings: Any) -> str:
    """Best-effort target root for optional, non-blocking lookups.

    Unlike `_target_project_root`, never raises when no root is resolvable —
    project-guidance discovery must degrade to "nothing found" rather than
    fail the run over a caller that simply hasn't supplied a project root yet.
    """

    context = _target_project_context_from_state(state)
    if context is not None and context.project_root:
        return context.project_root
    resolved = str(state.get("resolved_project_root", "")).strip()
    return resolved or settings.project_root


def request_plan_node(
    state: GraphState,
    progress_callback: Callable[[str], None] | None = None,
) -> GraphState:
    """Run the coding agent in planning mode to get a high-level implementation plan."""

    _log_node_start("5b", "REQUEST_PLAN", "Request implementation plan from coding agent")
    settings = load_settings()

    correction = state.get("plan_correction", "").strip()
    feedback = list(state.get("task_feedback", []))
    rejection_count = state.get("plan_rejection_count", 0)

    logger.info(
        "Plan request: rejection_count=%d feedback_items=%d",
        rejection_count,
        len(feedback),
    )
    if correction:
        logger.info("Plan correction feedback: %s", correction)
    if feedback:
        logger.info("Task feedback included: %s", " | ".join(feedback))

    plan_msg = (
        f"Requesting revised plan from coding agent (attempt {rejection_count + 1})..."
        if rejection_count > 0
        else "Requesting implementation plan from coding agent..."
    )
    logger.info(plan_msg)
    if progress_callback is not None:
        progress_callback(plan_msg)

    correction_section = (
        f"\nPrevious plan was rejected. Correction needed:\n{correction}" if correction else ""
    )
    feedback_section = (
        f"\nTask feedback for this attempt:\n{_format_bullets(feedback)}" if feedback else ""
    )
    project_guidance = list(state.get("project_guidance_notes", []))
    guidance_section = (
        "\nTarget project's own guidance:\n" + _format_bullets(project_guidance)
        if project_guidance
        else ""
    )
    instruction = render_prompt(
        PLAN_REQUEST_INSTRUCTION_PROMPT_KEY,
        formulated_task=state.get("formulated_task", "") or state["request"],
        task_feedback=feedback_section,
        correction_feedback=correction_section,
        project_guidance=guidance_section,
    )
    target_project_root = _target_project_root(state, settings)
    result = run_coding_agent(
        agent_instruction=instruction,
        project_root=target_project_root,
        settings=settings,
        sandbox_override="read-only",
    )
    plan_text = result.stdout.strip()
    plan_stderr = result.stderr.strip()
    logger.info(
        "Plan received (%d chars): %s",
        len(plan_text),
        _single_line_preview(plan_text, limit=360),
    )
    logger.info("Plan full text:\n%s", plan_text or "<empty>")
    logger.info("Plan request exit code: %s", result.returncode)
    if plan_stderr:
        logger.info(
            "Plan request stderr (%d chars): %s",
            len(plan_stderr),
            _single_line_preview(plan_stderr, limit=360),
        )
    if result.changed_files_delta:
        logger.warning("Plan request unexpectedly changed files: %s", result.changed_files_delta)
    if progress_callback is not None:
        progress_callback(_plan_share_message(plan_text))

    return {
        "plan_text": plan_text,
        "plan_agent_stderr": plan_stderr,
        "plan_agent_returncode": result.returncode,
        "plan_approved": False,
        "plan_correction": "",
    }


def _recheck_project_guidance_for_proposed_files(
    state: GraphState, settings: Any, plan_text: str
) -> tuple[list[str], bool]:
    """ATL-078: after the coding agent's plan names files/areas, recheck whether
    additional project guidance is now relevant — reuses discover_project_guidance
    unchanged, just with plan-enriched request text, so it's still bounded to the
    same three canonical files plus one skill and never scans the repository.
    """

    existing = list(state.get("project_guidance_notes", []))
    if not settings.project_guidance_discovery_enabled:
        return existing, False
    request_text = state.get("bounded_request", "") or state["request"]
    project_root = _soft_target_project_root(state, settings)
    refreshed = list(discover_project_guidance(f"{request_text}\n{plan_text}", project_root))
    if not refreshed:
        return existing, False
    merged = list(dict.fromkeys(existing + refreshed))
    return merged, merged != existing


def review_plan_node(
    state: GraphState,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Ask the orchestrator LLM whether the coding agent's plan correctly addresses the task."""

    _log_node_start("5c", "REVIEW_PLAN", "Review coding agent plan")
    settings = load_settings()

    if progress_callback is not None:
        progress_callback("Plan received. Reviewing...")

    plan_text = state.get("plan_text", "").strip()
    plan_agent_stderr = state.get("plan_agent_stderr", "").strip()
    plan_agent_returncode = state.get("plan_agent_returncode")
    formulated_task = state.get("formulated_task", "") or state["request"]
    rejection_count = state.get("plan_rejection_count", 0)

    context = _target_project_context_from_state(state)
    backlog_item = context.backlog_item if context is not None else None
    backlog_priority = backlog_item.priority if backlog_item is not None else ""
    backlog_size = backlog_item.size if backlog_item is not None else ""

    logger.info("Reviewing plan (rejection_count=%d):", rejection_count)
    logger.info("Plan text:\n%s", plan_text or "<empty>")

    if plan_agent_returncode not in (None, 0):
        diagnostic = plan_agent_stderr or plan_text or "No backend diagnostic was returned."
        reason = (
            "Coding backend could not produce an implementation plan "
            f"(exit code {plan_agent_returncode}): {_single_line_preview(diagnostic, limit=500)}"
        )
        logger.warning("Plan backend failure requires operator guidance: %s", reason)
        if progress_callback is not None:
            progress_callback("Plan backend failed. Human review is required.")
        return {
            "plan_approved": False,
            "plan_review_reason": reason,
            "plan_correction": "",
            "plan_rejection_count": rejection_count,
            "plan_needs_human_review": True,
        }

    project_guidance, guidance_changed = _recheck_project_guidance_for_proposed_files(
        state, settings, plan_text
    )
    guidance_updates: dict[str, Any] = {}
    if guidance_changed:
        logger.info(
            "[LEARN] Project guidance recheck after plan found additional relevant "
            "guidance: %d -> %d note(s)",
            len(state.get("project_guidance_notes", [])),
            len(project_guidance),
        )
        guidance_updates["project_guidance_notes"] = project_guidance

    decision, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.REVIEW_PLAN,
        operation=lambda: review_plan(
            formulated_task=formulated_task,
            plan_text=plan_text,
            agent_error=plan_agent_stderr,
            settings=settings,
            project_guidance=project_guidance,
            backlog_priority=backlog_priority,
            backlog_size=backlog_size,
        ),
    )
    if _budget_blocked(error):
        return {**guidance_updates, **budget_updates}
    if isinstance(error, PlanReviewUnavailable):
        logger.warning("Plan reviewer unavailable: %s — routing to human interrupt.", error)
        return {
            "plan_approved": False,
            "plan_review_reason": str(error),
            "plan_correction": "",
            "plan_needs_human_review": True,
            **guidance_updates,
            **budget_updates,
        }
    if error is not None:
        raise error
    assert decision is not None

    logger.info("Plan review decision: approved=%s reason=%s", decision.approved, decision.reason)
    if not decision.approved:
        logger.info("Plan correction guidance: %s", decision.correction)
        new_rejection_count = rejection_count + 1
        logger.info("Plan rejection count now: %d", new_rejection_count)
        if progress_callback is not None:
            progress_callback(f"Plan rejected (attempt {new_rejection_count}): {decision.reason}")
        return {
            "plan_approved": False,
            "plan_review_reason": decision.reason,
            "plan_correction": decision.correction,
            "plan_needs_human_review": False,
            "plan_rejection_count": new_rejection_count,
            "coding_agent_tier": decision.coding_agent_tier,
            **guidance_updates,
            **budget_updates,
        }

    logger.info("Plan approved. Coding-agent tier: %s", decision.coding_agent_tier)
    if progress_callback is not None:
        progress_callback("Plan approved. Starting implementation...")
    return {
        "plan_approved": True,
        "plan_review_reason": decision.reason,
        "plan_correction": "",
        "plan_needs_human_review": False,
        "coding_agent_tier": decision.coding_agent_tier,
        **guidance_updates,
        **budget_updates,
    }


def plan_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause and ask the human for plan guidance after repeated rejections."""

    _log_node_start("5d", "PLAN_INTERRUPT", "Pause for human plan guidance")
    plan_text = str(state.get("plan_text", "")).strip()
    reason = str(state.get("plan_review_reason", "")).strip()
    rejection_count = int(state.get("plan_rejection_count", 0))
    logger.info(
        "Plan interrupt: rejection_count=%d reason=%s",
        rejection_count,
        reason[:120],
    )
    result = interrupt(
        {
            "kind": "plan_guidance",
            "plan_text": plan_text,
            "reason": reason,
            "rejection_count": rejection_count,
        }
    )
    guidance = str(result) if not isinstance(result, dict) else str(result.get("text", result))
    logger.info(
        "Plan interrupt resumed: guidance_length=%d preview=%s",
        len(guidance),
        guidance[:120],
    )
    return {"task_feedback": [guidance], "plan_rejection_count": 0, "plan_correction": ""}


def route_after_review_plan(state: GraphState) -> str:
    """Route after plan review: proceed, loop with correction, or ask human."""

    _log_decision_start("5c", "ROUTE_AFTER_REVIEW_PLAN", "Route after plan review")

    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    if state.get("plan_needs_human_review"):
        logger.info("Decision: plan reviewer unavailable -> PLAN_INTERRUPT")
        return "human review needed"

    if state["plan_approved"]:
        logger.info("Decision: plan approved -> CREATE_AGENT_INSTRUCTION")
        return "plan approved"

    rejection_count = state.get("plan_rejection_count", 0)
    if rejection_count >= 2:
        logger.info("Decision: plan rejected %d times -> PLAN_INTERRUPT", rejection_count)
        return "human review needed"

    logger.info(
        "Decision: plan rejected (%d/2), sending correction -> REQUEST_PLAN",
        rejection_count,
    )
    return "revise plan"


_APPROVAL_ACTIONS = {"approve", "request_changes", "ask_question", "cancel"}


def _parse_approval_action(result: Any) -> str:
    """Return the requested action, failing closed to "cancel" on anything unrecognized.

    This is a human-approval gate, so an ambiguous or malformed resume value
    must never be treated as approval.
    """

    if isinstance(result, dict):
        action = str(result.get("action", "")).strip().lower()
        if action in _APPROVAL_ACTIONS:
            return action
    return "cancel"


def _extract_resume_field(result: Any, key: str) -> str:
    if isinstance(result, dict):
        return str(result.get(key, "")).strip()
    return ""


def _check_project_guidance_changed_on_resume(state: GraphState) -> dict[str, Any]:
    """ATL-078: an approval can sit paused for a long time. Compare the current
    selected-guidance hash against what was checkpointed when it was first
    selected (1d_check_project_guidance); if it changed, flag it so
    route_after_approval sends the run back through CHECK_PROJECT_GUIDANCE —
    the same discovery+governance step already used, not a new mechanism —
    instead of silently proceeding to plan/execute on stale guidance.
    """

    settings = load_settings()
    checkpointed_hash = str(state.get("project_guidance_hash", "")).strip()
    if not settings.project_guidance_discovery_enabled or not checkpointed_hash:
        return {"project_guidance_changed_on_resume": False}

    request_text = state.get("bounded_request", "") or state["request"]
    project_root = _soft_target_project_root(state, settings)
    fresh_notes = discover_project_guidance(request_text, project_root)
    _, fresh_hash = _guidance_paths_and_hash(list(fresh_notes))
    changed = bool(fresh_hash) and fresh_hash != checkpointed_hash
    if changed:
        logger.warning(
            "[LEARN] Project guidance changed since selection (hash %s -> %s) — "
            "re-planning against current guidance instead of proceeding.",
            checkpointed_hash[:12],
            fresh_hash[:12],
        )
    return {"project_guidance_changed_on_resume": changed}


def approval_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause for human approval, and handle request-changes/ask-question/cancel.

    Four resume actions: approve, request_changes, ask_question, cancel. An
    unrecognized resume value fails closed to cancel. request_changes appends
    to task_feedback and loops back to TECH_LEAD_ANALYSE to regenerate the
    proposal. ask_question answers using request/project/code/research context
    already in state, then loops back to this same node so the same four
    choices are shown again with the answer visible.
    """

    _log_node_start("4/7", "APPROVAL_INTERRUPT", "Pause for human approval")
    approval_reason = str(state.get("approval_reason", "")).strip()
    formulated_task = str(state.get("formulated_task", "")).strip()
    last_question = str(state.get("approval_last_question", "")).strip()
    last_answer = str(state.get("approval_last_answer", "")).strip()

    interrupt_value: dict[str, Any] = {
        "kind": "approval",
        "reason": approval_reason,
        "formulated_task": formulated_task,
    }
    if last_question:
        interrupt_value["last_question"] = last_question
        interrupt_value["last_answer"] = last_answer

    logger.info("Approval interrupt: reason=%s", approval_reason)
    result = interrupt(interrupt_value)
    action = _parse_approval_action(result)

    if action == "approve":
        approved_by = _extract_resume_field(result, "approved_by")
        logger.info("Approval interrupt resumed: action=approve approved_by=%s", approved_by)
        updates: dict[str, Any] = {
            "approved": True,
            "approved_by": approved_by,
            "approval_action": "approve",
            "approval_last_question": "",
            "approval_last_answer": "",
        }
        guidance_updates = _check_project_guidance_changed_on_resume(state)
        if guidance_updates.get("project_guidance_changed_on_resume"):
            # Don't let route_after_review_risk's "already approved" shortcut
            # reuse an approval that was granted against stale guidance — the
            # re-planning pass must earn a fresh approval decision.
            updates["approved"] = False
        updates.update(guidance_updates)
        return updates

    if action == "cancel":
        logger.info("Approval interrupt resumed: action=cancel")
        return {
            "approved": False,
            "approved_by": "",
            "approval_action": "cancel",
            "approval_last_question": "",
            "approval_last_answer": "",
        }

    if action == "request_changes":
        feedback = _extract_resume_field(result, "feedback")
        new_revision_count = int(state.get("approval_revision_count", 0)) + 1
        logger.info(
            "Approval interrupt resumed: action=request_changes revision_count=%d",
            new_revision_count,
        )
        return {
            "approved": False,
            "approval_action": "request_changes",
            "task_feedback": [feedback] if feedback else [],
            "approval_revision_count": new_revision_count,
            "approval_last_question": "",
            "approval_last_answer": "",
        }

    # action == "ask_question"
    question = _extract_resume_field(result, "question")
    settings = load_settings()
    request_text = state.get("bounded_request", "") or state["request"]
    context = _target_project_context_from_state(state)
    code_context_root = (
        context.project_root.strip()
        if context is not None and context.project_root.strip()
        else str(state.get("resolved_project_root", "")).strip() or None
    )
    code_context_text = format_code_context_for_prompt(
        collect_code_context(request_text, settings, project_root_override=code_context_root)
    )
    research_evidence = _format_research_evidence(
        state.get("research_source_titles", []),
        state.get("research_source_locations", []),
        state.get("research_source_summaries", []),
    )
    answer, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.APPROVAL_INTERRUPT,
        operation=lambda: answer_operator_question(
            question=question,
            request=request_text,
            target_project_context=context,
            code_context=code_context_text,
            research_evidence=research_evidence,
            settings=settings,
        ),
    )
    if _budget_blocked(error):
        return {
            "approval_action": "ask_question",
            "approval_last_question": question,
            "approval_last_answer": "",
            **budget_updates,
        }
    if error is not None:
        raise error
    assert answer is not None
    logger.info("Approval interrupt resumed: action=ask_question")
    return {
        "approval_action": "ask_question",
        "approval_last_question": question,
        "approval_last_answer": answer,
        **budget_updates,
    }


def discover_validation_command_node(state: GraphState) -> dict[str, Any]:
    """Work out the target project's canonical validation command before handoff.

    The AI Tech Lead must run this command itself after the coding agent finishes
    (ATL-039), so it has to be known before the handoff. Discovery is read-only.
    When it finds nothing, the router pauses at ``VALIDATION_COMMAND_INTERRUPT``
    to ask the operator for the command; only if the operator does not supply one
    within the bounded retry does the run proceed without it, and the completion
    gate then escalates to human verification.

    A command already in state (operator-supplied on a prior loop) is kept as-is,
    so the post-interrupt loop back through here does not overwrite it.
    """

    _log_node_start("5e", "DISCOVER_VALIDATION_COMMAND", "Discover project validation command")
    existing = str(state.get("validation_command", "")).strip()
    if existing:
        logger.info("[LEARN] Validation command already set (%s) — keeping it.",
                    state.get("validation_command_source", "unknown source"))
        return {}

    settings = load_settings()
    request_text = state.get("bounded_request", "") or state["request"]
    discovered = discover_validation_command(
        request_text, _soft_target_project_root(state, settings)
    )
    if discovered is None:
        logger.info("[LEARN] No canonical validation command found by discovery.")
        return {"validation_command": "", "validation_command_source": ""}
    logger.info(
        "[LEARN] Canonical validation command: %s (%s)",
        discovered.command,
        discovered.source,
    )
    return {
        "validation_command": discovered.command,
        "validation_command_source": discovered.source,
    }


def route_after_discover_validation_command(state: GraphState) -> str:
    """Proceed when a command is known; otherwise ask the operator once."""

    _log_decision_start(
        "5e", "ROUTE_AFTER_DISCOVER_VALIDATION_COMMAND", "Route after validation-command discovery"
    )
    if str(state.get("validation_command", "")).strip():
        logger.info("Decision: validation command known -> CREATE_AGENT_INSTRUCTION")
        return "command found"
    retry_count = int(state.get("validation_command_retry_count", 0))
    if retry_count >= VALIDATION_COMMAND_MAX_RETRIES:
        logger.info(
            "Decision: operator did not supply a validation command (%d/%d) -> "
            "CREATE_AGENT_INSTRUCTION; completion will require human verification.",
            retry_count,
            VALIDATION_COMMAND_MAX_RETRIES,
        )
        return "proceed without command"
    logger.info(
        "Decision: no validation command (%d/%d) -> VALIDATION_COMMAND_INTERRUPT",
        retry_count,
        VALIDATION_COMMAND_MAX_RETRIES,
    )
    return "ask operator"


def validation_command_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause before the handoff and ask the operator for the validation command.

    Same bounded pause/answer/retry shape as
    ``context_clarification_interrupt`` — the limit is enforced by
    ``route_after_discover_validation_command``, this node only asks and records.
    An unusable or empty answer leaves ``validation_command`` empty and marks the
    attempt exhausted; the run then proceeds and the completion gate routes to
    human verification.
    """

    _log_node_start(
        "5e1", "VALIDATION_COMMAND_INTERRUPT", "Pause for the project's validation command"
    )
    retry_count = int(state.get("validation_command_retry_count", 0))
    result = interrupt(
        {
            "kind": "validation_command",
            "question": (
                "I could not determine this project's validation command. "
                "Reply with the exact command the AI Tech Lead should run to "
                "validate the finished work (e.g. `uv run pytest -q`), or reply "
                "`skip` to let completion fall back to human verification."
            ),
            "reason": "The completion gate re-runs this command itself before accepting the work.",
            "retry_count": retry_count,
        }
    )
    answer = str(result) if not isinstance(result, dict) else str(result.get("text", result))
    command = sanitise_validation_command(answer) if answer.strip().lower() not in {
        "", "skip", "none", "no", "n/a"
    } else None
    if command:
        logger.info("Operator supplied validation command: %s", command)
        return {
            "validation_command": command,
            "validation_command_source": "operator-supplied",
            "validation_command_retry_count": retry_count + 1,
        }
    logger.info("Operator did not supply a usable validation command; proceeding without one.")
    return {
        "validation_command_retry_count": retry_count + 1,
        "validation_command_exhausted": True,
    }


def create_agent_instruction_node(state: GraphState) -> dict[str, Any]:
    """Create the bounded instruction package for the coding-agent process."""

    _log_node_start("5/7", "CREATE_AGENT_INSTRUCTION", "Create coding-agent instruction")
    settings = load_settings()
    correction = (
        state.get("coding_agent_correction", "").strip()
        or state.get("verification_correction", "").strip()
        or state.get("review_correction", "").strip()
        or None
    )
    if correction:
        logger.info(
            "Including correction in instruction (coding_agent_retry_count=%d, "
            "verification_attempt_count=%d)",
            state.get("coding_agent_retry_count", 0),
            state.get("verification_attempt_count", 0),
        )
    research_evidence = _format_research_evidence(
        state.get("research_source_titles", []),
        state.get("research_source_locations", []),
        state.get("research_source_summaries", []),
    )
    if state.get("research_evidence_required") and not research_evidence:
        research_evidence = ["No research evidence collected."]
    agent_instruction = build_agent_instruction(
        request=state["request"],
        brief=state["brief"],
        formulated_task=state.get("formulated_task", ""),
        task_feedback=list(state.get("task_feedback", [])),
        needs_approval=state["needs_approval"],
        approved=state["approved"],
        settings=settings,
        target_project_context=_target_project_context_from_state(state),
        project_root=_target_project_root(state, settings),
        research_evidence=research_evidence,
        agent_correction=correction,
        project_guidance=list(state.get("project_guidance_notes", [])),
        validation_command=state.get("validation_command", ""),
    )
    try:
        selected_hashes = selected_instruction_hashes(
            state["request"],
            project_root=_target_project_root(state, settings),
        )
    except (FileNotFoundError, OSError) as error:
        # The handoff above is authoritative. Version capture is additive
        # telemetry and must not break a valid workflow when a minimal target
        # project has no optional skills pack.
        logger.warning("Could not record selected instruction versions: %s", error)
        selected_hashes = {}

    logger.info(
        "Instruction purpose: merge task, brief, project rules, selected skills, "
        "allowed directories, stop conditions, and validation expectations."
    )
    logger.info("Instruction research evidence items: %d", len(research_evidence))
    logger.info("Instruction size: %s characters", len(agent_instruction))
    logger.info("Instruction preview: %s", _single_line_preview(agent_instruction, limit=360))

    return {
        "agent_instruction": agent_instruction,
        "selected_skill_paths": list(selected_hashes),
        "selected_skill_hashes": selected_hashes,
    }


def _log_node_start(step: str, node_name: str, description: str) -> None:
    logger.info(LOG_SEPARATOR)
    logger.info("NODE [%s] %s - %s", step, node_name, description)


def _log_decision_start(step: str, decision_name: str, description: str) -> None:
    logger.info(LOG_SEPARATOR)
    logger.info("DECISION [%s] %s - %s", step, decision_name, description)


def _task_git_state_from_state(state: GraphState) -> TaskGitState | None:
    worktree = str(state.get("git_task_worktree", "")).strip()
    if not worktree:
        return None
    return TaskGitState(
        canonical_root=str(state.get("git_task_canonical_root", "")).strip(),
        worktree=worktree,
        branch=str(state.get("git_task_branch", "")).strip(),
        base_sha=str(state.get("git_task_base_sha", "")).strip(),
        tip_sha=str(state.get("git_task_tip_sha", "")).strip(),
        task_commit_sha=str(state.get("git_task_commit_sha", "")).strip(),
        branch_pushed=bool(state.get("git_task_branch_pushed", False)),
        main_status=str(state.get("git_main_status", "")).strip(),
        main_sha=str(state.get("git_main_sha", "")).strip(),
        integration_status=str(state.get("git_integration_status", "")).strip(),
        integration_reason=str(state.get("git_integration_reason", "")).strip(),
    )


def _single_line_preview(text: str, limit: int = 240) -> str:
    normalized_text = " ".join(text.split())
    if len(normalized_text) <= limit:
        return normalized_text
    return f"{normalized_text[: limit - 3]}..."


def _plan_share_message(plan_text: str) -> str:
    """Format the coding-agent plan as a short, user-facing update."""

    normalized = plan_text.strip()
    if not normalized:
        return "Coding agent returned an empty plan."
    return f"Plan from coding agent:\n{normalized}"


def _format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _restart_required_for_changed_files(changed_files: tuple[str, ...]) -> bool:
    """Flag Python process restart when the agent touches local package code."""

    for changed_file in changed_files:
        normalized = changed_file.replace("\\", "/")
        parts = Path(normalized).parts
        if len(parts) >= 2 and parts[0] == "src" and parts[1] == "ai_tech_lead":
            return True
    return False


def _format_research_evidence(
    titles: list[str],
    locations: list[str],
    summaries: list[str],
) -> list[str]:
    evidence: list[str] = []
    for title, location, summary in zip(titles, locations, summaries, strict=False):
        note = f"{title} | {location}"
        if summary:
            note = f"{note} | {summary}"
        evidence.append(note)
    return evidence


def _short_reason(reason: str, limit: int = 120) -> str:
    """Return a single-line preview of the approval reason for log readability."""
    normalized = " ".join(reason.split())
    return normalized[:limit] + "..." if len(normalized) > limit else normalized


def _request_title(request: str) -> str:
    """Return a short task label for readable console logs."""

    for line in request.splitlines():
        if line.startswith("Backlog item:") or line.startswith("Title:"):
            return line.strip()
    return request.splitlines()[0][:120]


def run_coding_agent_node(
    state: GraphState,
    execute_coding_agent_override: bool | None = None,
    project_root_override: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
    cancellation_token: CodingAgentCancellationToken | None = None,
) -> GraphState:
    """Run the configured coding-agent command as a separate process.

    The configured coding agent may modify files on disk. Restart this Python process before relying
    on any Python code changed during the subprocess run.
    """

    _log_node_start("6/7", "RUN_CODING_AGENT", "Run configured coding agent")
    logger.info(
        "[LEARN] This is the RUN_CODING_AGENT node — the final step of the coding workflow graph."
    )
    logger.info("[LEARN] It launches the configured coding-agent backend as a separate subprocess.")

    settings = load_settings()
    if execute_coding_agent_override is not None:
        settings = replace(settings, execute_coding_agent=execute_coding_agent_override)
    target_project_root = _target_project_root(state, settings)
    if project_root_override is not None:
        override_root = Path(project_root_override).resolve()
        resolved_project_root = str(state.get("resolved_project_root", "")).strip()
        if resolved_project_root and override_root != target_project_root:
            raise ValueError(
                "project_root_override does not match the workflow's resolved target project root."
            )
        target_project_root = override_root

    # Reuse every already-approved, already-produced piece of workflow state
    # that can name a relevant file — the coding-agent instruction, plan, and
    # task description, plus the earlier read-only code-recon report and the
    # project-guidance locations already resolved for this task. No new LLM
    # call: this is all state the graph already computed this run.
    relevance_text = "\n".join(
        text
        for text in (
            state.get("agent_instruction", ""),
            state.get("plan_text", ""),
            state.get("formulated_task", ""),
            state.get("bounded_request", "") or state.get("request", ""),
            state.get("brief", ""),
            state.get("code_recon_report", ""),
            "\n".join(state.get("project_guidance_related_locations", []) or []),
        )
        if text
    )
    lifecycle_enabled = bool(settings.execute_coding_agent and state.get("request_id"))
    existing_task_git_state = _task_git_state_from_state(state) if lifecycle_enabled else None
    execution_root = target_project_root
    lifecycle_fields: dict[str, Any] = {"git_lifecycle_enabled": lifecycle_enabled}

    # The canonical preflight remains the authoritative dirty-work boundary.
    # The coding subprocess itself is then moved to the request-owned worktree.
    canonical_preflight = run_git_preflight(target_project_root, relevance_text)
    if not canonical_preflight.safe_to_proceed:
        preflight = canonical_preflight
    elif lifecycle_enabled:
        try:
            task_git_state = prepare_task_worktree(
                target_project_root,
                str(state.get("request_id", "")),
                existing=existing_task_git_state,
            )
            execution_root = Path(task_git_state.worktree)
            lifecycle_fields.update(
                {
                    "git_task_canonical_root": task_git_state.canonical_root,
                    "git_task_worktree": task_git_state.worktree,
                    "git_task_branch": task_git_state.branch,
                    "git_task_base_sha": task_git_state.base_sha,
                    "git_task_tip_sha": task_git_state.tip_sha,
                    "git_task_commit_sha": task_git_state.task_commit_sha,
                    "git_task_branch_pushed": task_git_state.branch_pushed,
                    "git_main_status": task_git_state.main_status,
                    "git_main_sha": task_git_state.main_sha,
                    "git_integration_status": task_git_state.integration_status,
                    "git_integration_reason": task_git_state.integration_reason,
                }
            )
            task_head = task_git_state.tip_sha or task_git_state.base_sha
            preflight = GitPreflightResult(
                status="task_worktree",
                branch=task_git_state.branch,
                head=task_head,
                dirty_paths=canonical_preflight.dirty_paths,
                reason=(
                    "Coding agent will run in the request-owned task worktree. "
                    f"Canonical preflight: {canonical_preflight.reason}"
                ),
            )
        except (GitLifecycleBlocked, GitLifecycleError) as error:
            logger.warning("Git lifecycle blocked coding-agent launch: %s", error)
            return {
                **lifecycle_fields,
                "git_preflight_status": "blocked_lifecycle",
                "git_preflight_branch": "",
                "git_preflight_head": "",
                "git_preflight_dirty_paths": canonical_preflight.dirty_paths,
                "git_preflight_reason": str(error),
                "coding_agent_result": str(error),
                "coding_agent_success": False,
                "coding_agent_changed_files": (),
                "restart_required": False,
                "coding_agent_correction": str(error),
                "coding_agent_command": "",
                "coding_agent_returncode": None,
                "coding_agent_timed_out": False,
                "coding_agent_performed_by": "",
                "coding_agent_validation": "",
            }
    else:
        preflight = canonical_preflight
    logger.info(
        "[LEARN] Git preflight: status=%s branch=%s head=%s dirty_paths=%d",
        preflight.status,
        preflight.branch,
        preflight.head,
        len(preflight.dirty_paths),
    )
    preflight_fields = {
        **lifecycle_fields,
        "git_preflight_status": preflight.status,
        "git_preflight_branch": preflight.branch,
        "git_preflight_head": preflight.head,
        "git_preflight_dirty_paths": preflight.dirty_paths,
        "git_preflight_reason": preflight.reason,
    }
    if not preflight.safe_to_proceed:
        logger.warning("Git preflight blocked the coding-agent launch: %s", preflight.reason)
        return {
            **preflight_fields,
            "coding_agent_result": preflight.reason,
            "coding_agent_success": False,
            "coding_agent_changed_files": (),
            "restart_required": False,
            "coding_agent_correction": preflight.reason,
            "coding_agent_command": "",
            "coding_agent_returncode": None,
            "coding_agent_timed_out": False,
            "coding_agent_performed_by": "",
            "coding_agent_validation": "",
        }

    # Approval context: explains who/what authorised this run
    needs_approval = state["needs_approval"]
    approved = state["approved"]
    if not needs_approval:
        approval_source = "not_required_by_risk_review"
    elif approved:
        approval_source = "human_approved"
    else:
        approval_source = "not_applicable"

    logger.info(
        "[LEARN] Approval context: approval_required=%s approved=%s approval_source=%s",
        needs_approval,
        approved,
        approval_source,
    )
    logger.info("[LEARN] Approval reason: %s", _short_reason(state["approval_reason"]))
    coding_agent_tier = state.get("coding_agent_tier", DEFAULT_CODING_AGENT_TIER)
    extra_args = resolve_tier_args(settings.coding_agent_command, coding_agent_tier)
    logger.info("Coding agent command: %s", settings.coding_agent_command)
    logger.info("Coding agent tier: %s (extra args: %s)", coding_agent_tier, extra_args)
    logger.info("Coding agent execution enabled: %s", settings.execute_coding_agent)
    logger.info("Working directory: %s", target_project_root)

    if settings.execute_coding_agent:
        logger.info("[LEARN] Starting coding agent subprocess now. This may take several minutes.")
        if progress_callback is not None:
            progress_callback(
                f"Running coding agent ({settings.coding_agent_command}). "
                "This may take several minutes..."
            )

    runner_kwargs = {
        "agent_instruction": state["agent_instruction"],
        "project_root": execution_root,
        "settings": settings,
        "progress_callback": progress_callback,
        "use_pty": True,
        "sandbox_override": "workspace-write",
        "extra_args": extra_args,
    }
    if cancellation_token is not None:
        runner_kwargs["cancellation_token"] = cancellation_token

    try:
        result = run_coding_agent(**runner_kwargs)
    except Exception:
        logger.exception(
            "Coding agent execution failed before producing a result project_root=%s",
            target_project_root,
        )
        raise
    command = getattr(result, "command", ())

    logger.info("[LEARN] Coding agent subprocess finished.")
    logger.info("[LEARN] %s", result.message)
    logger.info("System exit code: %s", result.returncode)

    if result.duration_seconds > 0:
        logger.info("[LEARN] Duration: %.1f seconds", result.duration_seconds)

    if result.changed_files_delta:
        logger.info(
            "[LEARN] Files changed by the coding agent (%s): %s",
            len(result.changed_files_delta),
            ", ".join(result.changed_files_delta[:10]),
        )
    elif result.command:
        logger.info("[LEARN] No new file changes detected after coding agent run.")

    logger.info(
        "[LEARN] Token usage: not available from the current configured coding-agent backend. "
        "The subprocess runs as a separate process and does not expose its LLM token counts."
    )
    logger.info("[LEARN] Cost: not available for the same reason.")

    report = normalize_coding_agent_result(result)
    changed_files = report.changed_files
    restart_required = _restart_required_for_changed_files(changed_files)
    success = report.success
    retry_count = state.get("coding_agent_retry_count", 0)
    new_retry_count = 0 if success else retry_count + 1
    correction = "" if success else result.summary()
    logger.info(
        "Coding agent success=%s retry_count=%d -> %d",
        success,
        retry_count,
        new_retry_count,
    )
    logger.info("Restart required: %s", restart_required)
    logger.info("Validation reported: %s", report.validation or "not stated")

    return {
        **preflight_fields,
        "coding_agent_result": result.summary(),
        "coding_agent_success": success,
        "coding_agent_changed_files": changed_files,
        "restart_required": restart_required,
        "coding_agent_retry_count": new_retry_count,
        "coding_agent_correction": correction,
        "coding_agent_command": command[0] if command else "",
        "coding_agent_returncode": report.returncode,
        "coding_agent_timed_out": getattr(result, "timed_out", False),
        "coding_agent_performed_by": settings.coding_agent_command if command else "",
        "coding_agent_validation": report.validation,
    }


def failure_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause for human guidance after repeated coding agent failures."""

    _log_node_start("6b", "FAILURE_INTERRUPT", "Pause for human guidance after repeated failures")
    result_summary = str(state.get("coding_agent_result", "")).strip()
    retry_count = int(state.get("coding_agent_retry_count", 0))
    logger.info("Failure interrupt: retry_count=%d result=%s", retry_count, result_summary[:120])
    result = interrupt(
        {
            "kind": "failure_guidance",
            "coding_agent_result": result_summary,
            "retry_count": retry_count,
        }
    )
    guidance = str(result) if not isinstance(result, dict) else str(result.get("text", result))
    logger.info(
        "Failure interrupt resumed: guidance_length=%d preview=%s",
        len(guidance),
        guidance[:120],
    )
    return {
        "task_feedback": [guidance],
        "coding_agent_retry_count": 0,
        "coding_agent_correction": "",
    }


def route_after_run_coding_agent(state: GraphState) -> str:
    """Route after coding agent: success → end, failure → retry or human interrupt."""

    _log_decision_start("6", "ROUTE_AFTER_RUN_CODING_AGENT", "Route after coding agent run")

    if str(state.get("git_preflight_status", "")).startswith("blocked"):
        logger.info(
            "Decision: git preflight %s -> FAILURE_INTERRUPT", state.get("git_preflight_status")
        )
        return "retries exhausted"

    if state.get("coding_agent_success"):
        logger.info("Decision: success -> CAPTURE_VALIDATION_EVIDENCE")
        return "coding succeeded"

    retry_count = state.get("coding_agent_retry_count", 0)
    if retry_count < 2:
        logger.info("Decision: failure retry_count=%d < 2 -> CREATE_AGENT_INSTRUCTION", retry_count)
        return "retry coding"

    logger.info("Decision: failure retry_count=%d >= 2 -> FAILURE_INTERRUPT", retry_count)
    return "retries exhausted"


def _completion_relevance_text(state: GraphState) -> str:
    """Already-approved workflow text that can name a file relevant to this task.

    Mirrors the relevance corpus RUN_CODING_AGENT builds for its git preflight —
    no new LLM call, just state the graph already produced this run.
    """

    return "\n".join(
        text
        for text in (
            state.get("agent_instruction", ""),
            state.get("plan_text", ""),
            state.get("formulated_task", ""),
            state.get("bounded_request", "") or state.get("request", ""),
            state.get("brief", ""),
            state.get("code_recon_report", ""),
            "\n".join(state.get("project_guidance_related_locations", []) or []),
        )
        if text
    )


def capture_validation_evidence_node(state: GraphState) -> dict[str, Any]:
    """Gather ATL's own completion evidence before the verifier decides (ATL-039).

    Runs in whichever checkout the coding agent used — the request-owned task
    worktree when the Git lifecycle is active, otherwise the resolved target
    project root. Builds a git diff summary, runs the discovered canonical
    validation command itself (``shell=False``), and lists changed files that
    look unrelated to the approved task. ``validation_passed=None`` here means
    ATL had no command or could not run one; the verifier turns that into human
    verification, never a pass.
    """

    _log_node_start(
        "6b1", "CAPTURE_VALIDATION_EVIDENCE", "Capture diff and run project validation"
    )
    settings = load_settings()
    if not settings.execute_coding_agent:
        # Instruction-only mode: no coding agent actually ran, so there is
        # nothing on disk for ATL to independently validate. Leave
        # validation_passed unset — the verifier treats that as
        # "cannot prove completion" rather than a pass.
        logger.info("[LEARN] Coding execution disabled — skipping validation evidence capture.")
        return {
            "diff_summary": "",
            "git_changed_files": [],
            "validation_passed": None,
            "validation_output_tail": "Coding execution disabled; no work to validate.",
            "out_of_scope_paths": [],
        }
    worktree = str(state.get("git_task_worktree", "")).strip()
    checkout_root = Path(worktree) if worktree else _target_project_root(state, settings)
    base_sha = (
        str(state.get("git_task_base_sha", "")).strip()
        or str(state.get("git_preflight_head", "")).strip()
    )
    evidence = capture_completion_evidence(
        checkout_root=checkout_root,
        base_sha=base_sha,
        validation_command=state.get("validation_command", ""),
        relevance_text=_completion_relevance_text(state),
        timeout_seconds=settings.max_runtime_minutes * 60,
    )
    logger.info(
        "[LEARN] Completion evidence: validation_passed=%s command=%s out_of_scope=%d",
        evidence.validation_passed,
        evidence.validation_command or "(none)",
        len(evidence.out_of_scope_paths),
    )
    if evidence.out_of_scope_paths:
        logger.warning(
            "[LEARN] Changed files unrelated to the approved task: %s",
            ", ".join(evidence.out_of_scope_paths),
        )
    return {
        "diff_summary": evidence.diff_summary,
        "git_changed_files": list(evidence.changed_files),
        "validation_passed": evidence.validation_passed,
        "validation_output_tail": evidence.validation_output_tail,
        "out_of_scope_paths": list(evidence.out_of_scope_paths),
    }


def implementation_review_node(state: GraphState) -> dict[str, Any]:
    """Independent second-agent review of the completed change (ATL-093).

    Runs only for meaningful or risky work (``review_required``). A genuinely
    different coding agent reviews the git-derived diff read-only against the
    approved task/plan/criteria and the ATL-039 validation result, and returns
    structured findings. Required findings drive one bounded correction cycle;
    an unavailable, failed, mutated, or still-unresolved review is turned by the
    verifier into human verification, never Complete.
    """

    _log_node_start("6b2", "IMPLEMENTATION_REVIEW", "Independent second-agent review")
    settings = load_settings()

    if not settings.execute_coding_agent:
        logger.info("[LEARN] Coding execution disabled — skipping implementation review.")
        return {"review_status": IMPLEMENTATION_REVIEW_SKIPPED}

    tier = str(state.get("coding_agent_tier", DEFAULT_CODING_AGENT_TIER))
    risk_level = str(state.get("risk_level", ""))
    risk_signal_text = "\n".join(
        text
        for text in (
            state.get("formulated_task", ""),
            state.get("brief", ""),
            "\n".join(state.get("git_changed_files", []) or []),
        )
        if text
    )
    if not review_required(
        coding_agent_tier=tier,
        risk_level=risk_level,
        risk_signal_text=risk_signal_text,
        settings=settings,
    ):
        logger.info(
            "[LEARN] Implementation review not required (tier=%s risk=%s).", tier, risk_level
        )
        return {"review_status": IMPLEMENTATION_REVIEW_SKIPPED}

    keywords = matched_risk_keywords(risk_signal_text, settings)
    logger.info(
        "[LEARN] Implementation review required (tier=%s risk=%s keywords=%s).",
        tier,
        risk_level,
        ",".join(keywords) or "none",
    )

    worktree = str(state.get("git_task_worktree", "")).strip()
    checkout_root = Path(worktree) if worktree else _target_project_root(state, settings)
    outcome = run_implementation_review(
        formulated_task=state.get("formulated_task", ""),
        plan_text=state.get("plan_text", ""),
        acceptance_criteria=settings.acceptance_criteria,
        diff_summary=state.get("diff_summary", ""),
        changed_files=tuple(state.get("git_changed_files", []) or ()),
        validation_command=state.get("validation_command", ""),
        validation_passed=state.get("validation_passed"),
        validation_output_tail=state.get("validation_output_tail", ""),
        checkout_root=checkout_root,
        settings=settings,
    )
    correction_count = int(state.get("review_correction_count", 0))
    logger.info(
        "[LEARN] Implementation review: status=%s findings=%d reviewer=%s correction_count=%d",
        outcome.status,
        len(outcome.findings),
        outcome.performed_by or "(none)",
        correction_count,
    )

    updates: dict[str, Any] = {
        "review_findings": list(outcome.findings),
        "review_agent_performed_by": outcome.performed_by,
    }
    if (
        outcome.status == IMPLEMENTATION_REVIEW_FINDINGS
        and correction_count >= IMPLEMENTATION_REVIEW_MAX_CORRECTIONS
    ):
        logger.info(
            "Decision: review findings still present after %d correction(s) -> findings_unresolved",
            IMPLEMENTATION_REVIEW_MAX_CORRECTIONS,
        )
        updates["review_status"] = IMPLEMENTATION_REVIEW_FINDINGS_UNRESOLVED
        return updates

    updates["review_status"] = outcome.status
    if outcome.status == IMPLEMENTATION_REVIEW_FINDINGS:
        updates["review_correction"] = outcome.correction
        updates["review_correction_count"] = correction_count + 1
    else:
        # Clear any correction carried from a prior review cycle so it cannot be
        # re-sent by a later verifier-driven correction.
        updates["review_correction"] = ""
    return updates


def route_after_implementation_review(state: GraphState) -> str:
    """Required findings (first cycle) loop back to re-implement; everything else verifies."""

    _log_decision_start(
        "6b2", "ROUTE_AFTER_IMPLEMENTATION_REVIEW", "Route after independent review"
    )
    if state.get("review_status") == IMPLEMENTATION_REVIEW_FINDINGS:
        logger.info("Decision: review findings -> CREATE_AGENT_INSTRUCTION")
        return "correction needed"
    logger.info(
        "Decision: review status=%s -> VERIFY_COMPLETION", state.get("review_status")
    )
    return "review finished"


def verify_completion_node(state: GraphState) -> dict[str, Any]:
    """AI Tech Lead verifies the coding agent's completed work against the approved task."""

    _log_node_start("6c", "VERIFY_COMPLETION", "Verify completed work against the approved task")
    settings = load_settings()
    attempt_count = state.get("verification_attempt_count", 0)
    prior_correction = state.get("verification_correction", "")
    target_project_context = _target_project_context_from_state(state)

    decision, error, budget_updates = _run_budgeted_orchestrator_call(
        state,
        settings,
        resume_node=NodeName.VERIFY_COMPLETION,
        operation=lambda: verify_completion(
            bounded_request=state.get("bounded_request", "") or state["request"],
            formulated_task=state.get("formulated_task", ""),
            brief=state.get("brief", ""),
            plan_text=state.get("plan_text", ""),
            acceptance_criteria=settings.acceptance_criteria,
            changed_files=tuple(
                state.get("git_changed_files", [])
                or state.get("coding_agent_changed_files", ())
            ),
            coding_agent_result=state.get("coding_agent_result", ""),
            diff_summary=state.get("diff_summary", ""),
            validation_command=state.get("validation_command", ""),
            validation_passed=state.get("validation_passed"),
            validation_output_tail=state.get("validation_output_tail", ""),
            out_of_scope_paths=tuple(state.get("out_of_scope_paths", []) or ()),
            review_status=str(state.get("review_status", "")),
            review_findings=tuple(state.get("review_findings", []) or ()),
            target_project_context=target_project_context,
            settings=settings,
            prior_correction=prior_correction,
            project_guidance=list(state.get("project_guidance_notes", [])),
        ),
    )
    if _budget_blocked(error):
        return budget_updates
    if isinstance(error, CompletionVerificationUnavailable):
        logger.warning("Completion verification unavailable: %s", error)
        return {
            "verification_status": "human_verification_required",
            "verification_reason": f"Verification unavailable: {error}",
            "verification_correction": "",
            **budget_updates,
        }
    if error is not None:
        raise error
    assert decision is not None

    logger.info(
        "Completion verification decision: status=%s attempt_count=%d reason=%s",
        decision.status,
        attempt_count,
        _short_reason(decision.reason),
    )

    if (
        decision.status == "correction_required"
        and attempt_count >= COMPLETION_VERIFICATION_MAX_CORRECTIONS
    ):
        # ATL-039: one automatic correction cycle only. A still-unresolved issue
        # escalates to human verification (the acceptance criterion), not a
        # terminal Failed — Failed is reserved for a human rejecting the work at
        # the verification interrupt.
        logger.info(
            "Decision: correction limit reached (%d) -> human_verification_required",
            COMPLETION_VERIFICATION_MAX_CORRECTIONS,
        )
        return {
            "verification_status": "human_verification_required",
            "verification_reason": (
                "Still unresolved after one automatic correction cycle: "
                f"{decision.reason}"
            ),
            "verification_correction": "",
            **budget_updates,
        }

    updates: dict[str, Any] = {
        **budget_updates,
        "verification_status": decision.status,
        "verification_reason": decision.reason,
        "verification_correction": (
            decision.correction if decision.status == "correction_required" else ""
        ),
    }
    if decision.status == "correction_required":
        updates["verification_attempt_count"] = attempt_count + 1
    return updates


def completion_verification_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause for a human decision when completion cannot be proven automatically."""

    _log_node_start(
        "6d", "COMPLETION_VERIFICATION_INTERRUPT", "Pause for human completion decision"
    )
    reason = state.get("verification_reason", "")
    result = interrupt(
        {
            "kind": "completion_verification",
            "reason": reason,
            "coding_agent_result": state.get("coding_agent_result", ""),
            "changed_files": list(state.get("coding_agent_changed_files", ())),
        }
    )
    decision = result.get("decision") if isinstance(result, dict) else None
    if decision == "confirm_complete":
        logger.info("Completion verification interrupt resumed: confirmed complete by human")
        return {
            "verification_status": "complete",
            "verification_reason": "Confirmed complete by human verification.",
        }

    rejection_text = (
        str(result.get("text", "")).strip() if isinstance(result, dict) else str(result)
    )
    logger.info(
        "Completion verification interrupt resumed: rejected, reason=%s", rejection_text[:120]
    )
    return {
        "verification_status": "failed",
        "verification_reason": rejection_text or "Rejected by human verification.",
    }


def route_after_verify_completion(state: GraphState) -> str:
    """Route after completion verification: complete/failed -> end, unclear -> human, else retry."""

    _log_decision_start(
        "6c", "ROUTE_AFTER_VERIFY_COMPLETION", "Route after completion verification"
    )
    if _route_to_run_budget_interrupt(state):
        return "budget exceeded"
    status = state.get("verification_status", "")

    if status == "human_verification_required":
        logger.info("Decision: human_verification_required -> COMPLETION_VERIFICATION_INTERRUPT")
        return "human verification needed"

    if status == "correction_required":
        logger.info("Decision: correction_required -> CREATE_AGENT_INSTRUCTION")
        return "correction needed"

    if (
        status == "complete"
        and state.get("git_lifecycle_enabled")
        and state.get("git_task_worktree")
    ):
        logger.info("Decision: verified task branch -> FINALIZE_TASK_BRANCH")
        return "finalize task branch"

    logger.info("Decision: %s -> END_NODE", status or "complete")
    return "verification finished"


def _task_git_state_updates(task: TaskGitState) -> dict[str, Any]:
    return {
        "git_task_canonical_root": task.canonical_root,
        "git_task_worktree": task.worktree,
        "git_task_branch": task.branch,
        "git_task_base_sha": task.base_sha,
        "git_task_tip_sha": task.tip_sha,
        "git_task_commit_sha": task.task_commit_sha,
        "git_task_branch_pushed": task.branch_pushed,
        "git_main_status": task.main_status,
        "git_main_sha": task.main_sha,
        "git_integration_status": task.integration_status,
        "git_integration_reason": task.integration_reason,
    }


def finalize_task_branch_node(state: GraphState) -> dict[str, Any]:
    """Commit and push the validated task branch before asking about main."""

    _log_node_start("6e", "FINALIZE_TASK_BRANCH", "Commit and push validated task branch")
    task = _task_git_state_from_state(state)
    if task is None:
        return {
            "git_integration_status": "blocked",
            "git_integration_reason": "No request-owned task worktree was recorded.",
        }
    try:
        pushed = commit_and_push_task_branch(task)
    except GitLifecycleError as error:
        logger.warning("Task branch finalization blocked: %s", error)
        return {
            "git_integration_status": "blocked",
            "git_integration_reason": str(error),
            "git_main_status": not_in_main_status(task.branch),
        }
    return _task_git_state_updates(pushed)


def route_after_finalize_task_branch(state: GraphState) -> str:
    if state.get("git_integration_status") == "awaiting_approval":
        return "integration approval"
    return "integration blocked"


def integration_approval_node(state: GraphState) -> dict[str, Any]:
    """Pause after validation and branch push until main integration is approved."""

    _log_node_start("6f", "INTEGRATION_APPROVAL", "Await approval to integrate into main")
    task = _task_git_state_from_state(state)
    if task is None:
        return {
            "git_integration_status": "blocked",
            "git_integration_reason": "No request-owned task worktree was recorded.",
        }
    result = interrupt(
        {
            "kind": "integration_approval",
            "task_branch": task.branch,
            "task_commit_sha": task.task_commit_sha,
            "main_status": not_in_main_status(task.branch),
            "prompt": (
                f"Validated work is pushed on {task.branch}, but it is NOT in main. "
                "Approve integration to current origin/main?"
            ),
        }
    )
    action = _parse_approval_action(result)
    if action == "cancel":
        logger.info("Integration approval declined for branch=%s", task.branch)
        return {
            "git_integration_status": "not_requested",
            "git_integration_reason": "Integration was not approved.",
            "git_main_status": not_in_main_status(task.branch),
        }
    if action != "approve":
        return {
            "git_integration_status": "blocked",
            "git_integration_reason": "Integration approval was not recognised.",
            "git_main_status": not_in_main_status(task.branch),
        }

    try:
        integrated = integrate_task_branch(
            task,
            validate_integrated=validate_integrated_git_result,
        )
    except GitLifecycleError as error:
        logger.warning("Main integration stopped: %s", error)
        return {
            "git_integration_status": "blocked",
            "git_integration_reason": str(error),
            "git_main_status": not_in_main_status(task.branch),
        }
    return _task_git_state_updates(integrated)


def end_node(state: GraphState) -> dict[str, Any]:
    """Log the final request and approval status for review."""

    _log_node_start("7/7", "END_NODE", "Workflow complete")
    logger.info("Task: %s", _request_title(state["request"]))
    if not state.get("atl_relevant", True):
        logger.info("Stopped early: not ATL-relevant — %s", state.get("atl_relevance_reason", ""))
    logger.info("Approval: required=%s approved=%s", state["needs_approval"], state["approved"])
    logger.info("Approved by: %s", state.get("approved_by", "") or "not required")
    logger.info("Performed by: %s", state.get("coding_agent_performed_by", "") or "not run")
    logger.info("Retries: %s", state.get("coding_agent_retry_count", 0))
    coding_agent_result = state.get("coding_agent_result", "").strip()
    if coding_agent_result:
        logger.info("Coding agent: %s", coding_agent_result.splitlines()[0])
    else:
        logger.info("Coding agent: not run")
    verification_status = state.get("verification_status", "")
    if verification_status:
        logger.info(
            "Completion verification: %s — %s",
            verification_status,
            _short_reason(state.get("verification_reason", "")),
        )
    main_status = str(state.get("git_main_status", "")).strip()
    if main_status:
        logger.info(main_status)
    logger.info("---END---")

    context_clarification_exhausted = bool(
        state.get("orchestrator_input_required")
    ) and (
        int(state.get("context_clarification_retry_count", 0))
        >= CONTEXT_CLARIFICATION_MAX_RETRIES
    )

    return {
        "restart_required": state.get("restart_required", False),
        "context_clarification_exhausted": context_clarification_exhausted,
    }


def _target_project_context_from_state(state: GraphState) -> TargetProjectContext | None:
    payload = state.get("target_project_context")
    return TargetProjectContext.from_payload(payload if isinstance(payload, dict) else None)


def build_graph(
    checkpointer_storage=None,
    execute_coding_agent_override: bool | None = None,
    project_root_override: str | None = None,
    coding_agent_progress_callback: Callable[[str], None] | None = None,
    workflow_progress_callback: Callable[[str, str], None] | None = None,
    coding_agent_cancellation_token: CodingAgentCancellationToken | None = None,
):
    """Build and compile the backlog-to-agent-instruction workflow."""

    workflow = StateGraph(GraphState)

    def with_progress(node, phase: str, summary: str):
        if workflow_progress_callback is None:
            return node

        def wrapped(state):
            workflow_progress_callback(phase, summary)
            return node(state)

        return wrapped

    workflow.add_node(
        NodeName.READ_REQUEST,
        with_progress(read_and_classify_request_node, "understanding_request", "Understanding the request."),
    )
    workflow.add_node(NodeName.PROJECT_SCOPE_DECISION, with_progress(project_scope_decision_node, "resolving_context", "Resolving project scope and context."))
    workflow.add_node(NodeName.RESOLVE_CONTEXT, with_progress(resolve_context_node, "resolving_context", "Resolving project scope and context."))
    workflow.add_node(
        NodeName.CONTEXT_CLARIFICATION_INTERRUPT, context_clarification_interrupt_node
    )
    workflow.add_node(NodeName.CHECK_RESEARCH, with_progress(check_research_node, "research_check", "Checking whether external research is needed."))
    workflow.add_node(NodeName.CHECK_CODE_LOOK_NEED, with_progress(check_code_look_need_node, "analysing", "Assessing the technical context needed for the task."))
    workflow.add_node(
        NodeName.CODEX_READS_CODE,
        lambda state: codex_reads_code_node(
            state, progress_callback=coding_agent_progress_callback
        ),
    )
    workflow.add_node(NodeName.CHECK_PROJECT_GUIDANCE, check_project_guidance_node)
    workflow.add_node(NodeName.PROJECT_GUIDANCE_INTERRUPT, project_guidance_interrupt_node)
    workflow.add_node(NodeName.DISCOVER_RESEARCH_SOURCE, discover_research_source_node)
    workflow.add_node(NodeName.RESEARCH_INTERRUPT, research_interrupt_node)
    workflow.add_node(NodeName.COLLECT_RESEARCH_EVIDENCE, collect_research_evidence_node)
    workflow.add_node(NodeName.REVIEW_RISK, with_progress(review_risk_node, "risk_review", "Reviewing execution risk and approval requirements."))
    workflow.add_node(NodeName.TECH_LEAD_ANALYSE, with_progress(tech_lead_analyse_node, "analysing", "Analysing the task and setting technical direction."))
    workflow.add_node(NodeName.APPROVAL_INTERRUPT, approval_interrupt_node)
    workflow.add_node(
        NodeName.REQUEST_PLAN,
        lambda state: request_plan_node(state, progress_callback=coding_agent_progress_callback),
    )
    workflow.add_node(
        NodeName.REVIEW_PLAN,
        lambda state: review_plan_node(state, progress_callback=coding_agent_progress_callback),
    )
    workflow.add_node(NodeName.PLAN_INTERRUPT, plan_interrupt_node)
    workflow.add_node(
        NodeName.DISCOVER_VALIDATION_COMMAND,
        with_progress(
            discover_validation_command_node,
            "analysing",
            "Finding the project's validation command.",
        ),
    )
    workflow.add_node(
        NodeName.VALIDATION_COMMAND_INTERRUPT, validation_command_interrupt_node
    )
    workflow.add_node(
        NodeName.CREATE_AGENT_INSTRUCTION,
        create_agent_instruction_node,
    )
    workflow.add_node(
        NodeName.RUN_CODING_AGENT,
        lambda state: run_coding_agent_node(
            state,
            execute_coding_agent_override=execute_coding_agent_override,
            project_root_override=project_root_override,
            progress_callback=coding_agent_progress_callback,
            cancellation_token=coding_agent_cancellation_token,
        ),
    )
    workflow.add_node(NodeName.FAILURE_INTERRUPT, failure_interrupt_node)
    workflow.add_node(
        NodeName.CAPTURE_VALIDATION_EVIDENCE,
        with_progress(
            capture_validation_evidence_node,
            "validating",
            "Running the project's validation command.",
        ),
    )
    workflow.add_node(
        NodeName.IMPLEMENTATION_REVIEW,
        with_progress(
            implementation_review_node,
            "validating",
            "Independent review of the change by a second agent.",
        ),
    )
    workflow.add_node(NodeName.VERIFY_COMPLETION, with_progress(verify_completion_node, "validating", "Validating the completed work."))
    workflow.add_node(NodeName.RUN_BUDGET_INTERRUPT, run_budget_interrupt_node)
    workflow.add_node(
        NodeName.COMPLETION_VERIFICATION_INTERRUPT, completion_verification_interrupt_node
    )
    workflow.add_node(NodeName.FINALIZE_TASK_BRANCH, finalize_task_branch_node)
    workflow.add_node(NodeName.INTEGRATION_APPROVAL, integration_approval_node)
    workflow.add_node(NodeName.END_NODE, with_progress(end_node, "finalising", "Finalising the specialist result."))

    workflow.add_edge(START, NodeName.READ_REQUEST)
    workflow.add_conditional_edges(
        NodeName.READ_REQUEST,
        route_after_read_and_classify_request,
        {
            "relevant": NodeName.PROJECT_SCOPE_DECISION,
            "not relevant": NodeName.END_NODE,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.PROJECT_SCOPE_DECISION, NodeName.RESOLVE_CONTEXT)
    workflow.add_conditional_edges(
        NodeName.RESOLVE_CONTEXT,
        route_after_resolve_context,
        {
            "context found": NodeName.CHECK_CODE_LOOK_NEED,
            "clarification needed": NodeName.CONTEXT_CLARIFICATION_INTERRUPT,
            "clarification limit reached": NodeName.END_NODE,
            "backlog item not found": NodeName.END_NODE,
        },
    )
    workflow.add_edge(NodeName.CONTEXT_CLARIFICATION_INTERRUPT, NodeName.RESOLVE_CONTEXT)
    workflow.add_conditional_edges(
        NodeName.CHECK_CODE_LOOK_NEED,
        route_after_check_code_look_need,
        {
            "needed": NodeName.CODEX_READS_CODE,
            "not needed": NodeName.CHECK_PROJECT_GUIDANCE,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.CODEX_READS_CODE, NodeName.CHECK_PROJECT_GUIDANCE)
    workflow.add_conditional_edges(
        NodeName.CHECK_PROJECT_GUIDANCE,
        route_after_check_project_guidance,
        {
            "review needed": NodeName.PROJECT_GUIDANCE_INTERRUPT,
            "no review needed": NodeName.TECH_LEAD_ANALYSE,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_conditional_edges(
        NodeName.PROJECT_GUIDANCE_INTERRUPT,
        route_after_project_guidance_interrupt,
        {
            "approved": NodeName.TECH_LEAD_ANALYSE,
            "rejected": NodeName.END_NODE,
        },
    )
    workflow.add_conditional_edges(
        NodeName.TECH_LEAD_ANALYSE,
        route_after_tech_lead_analyse,
        {
            "check research": NodeName.CHECK_RESEARCH,
            "analysis complete": NodeName.REVIEW_RISK,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_conditional_edges(
        NodeName.CHECK_RESEARCH,
        route_after_check_research,
        {
            "research needed": NodeName.DISCOVER_RESEARCH_SOURCE,
            "no research needed": NodeName.TECH_LEAD_ANALYSE,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_conditional_edges(
        NodeName.DISCOVER_RESEARCH_SOURCE,
        route_after_discover_research_source,
        {
            "research interrupt": NodeName.RESEARCH_INTERRUPT,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_conditional_edges(
        NodeName.RESEARCH_INTERRUPT,
        route_after_research_interrupt,
        {
            "approved": NodeName.COLLECT_RESEARCH_EVIDENCE,
            "declined": NodeName.END_NODE,
        },
    )
    workflow.add_edge(NodeName.COLLECT_RESEARCH_EVIDENCE, NodeName.TECH_LEAD_ANALYSE)
    workflow.add_conditional_edges(
        NodeName.REVIEW_RISK,
        route_after_review_risk,
        {
            "approval required": NodeName.APPROVAL_INTERRUPT,
            "already approved": NodeName.REQUEST_PLAN,
            "low risk": NodeName.REQUEST_PLAN,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_conditional_edges(
        NodeName.APPROVAL_INTERRUPT,
        route_after_approval,
        {
            "approved": NodeName.REQUEST_PLAN,
            "changes requested": NodeName.TECH_LEAD_ANALYSE,
            "question asked": NodeName.APPROVAL_INTERRUPT,
            "cancelled": NodeName.END_NODE,
            "revision limit reached": NodeName.END_NODE,
            "guidance changed": NodeName.CHECK_PROJECT_GUIDANCE,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.REQUEST_PLAN, NodeName.REVIEW_PLAN)
    workflow.add_conditional_edges(
        NodeName.REVIEW_PLAN,
        route_after_review_plan,
        {
            "plan approved": NodeName.DISCOVER_VALIDATION_COMMAND,
            "revise plan": NodeName.REQUEST_PLAN,
            "human review needed": NodeName.PLAN_INTERRUPT,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.PLAN_INTERRUPT, NodeName.REQUEST_PLAN)

    workflow.add_conditional_edges(
        NodeName.DISCOVER_VALIDATION_COMMAND,
        route_after_discover_validation_command,
        {
            "command found": NodeName.CREATE_AGENT_INSTRUCTION,
            "ask operator": NodeName.VALIDATION_COMMAND_INTERRUPT,
            "proceed without command": NodeName.CREATE_AGENT_INSTRUCTION,
        },
    )
    workflow.add_edge(
        NodeName.VALIDATION_COMMAND_INTERRUPT, NodeName.DISCOVER_VALIDATION_COMMAND
    )
    workflow.add_edge(NodeName.CREATE_AGENT_INSTRUCTION, NodeName.RUN_CODING_AGENT)
    workflow.add_conditional_edges(
        NodeName.RUN_CODING_AGENT,
        route_after_run_coding_agent,
        {
            "coding succeeded": NodeName.CAPTURE_VALIDATION_EVIDENCE,
            "retry coding": NodeName.CREATE_AGENT_INSTRUCTION,
            "retries exhausted": NodeName.FAILURE_INTERRUPT,
        },
    )
    workflow.add_edge(
        NodeName.CAPTURE_VALIDATION_EVIDENCE, NodeName.IMPLEMENTATION_REVIEW
    )
    workflow.add_conditional_edges(
        NodeName.IMPLEMENTATION_REVIEW,
        route_after_implementation_review,
        {
            "correction needed": NodeName.CREATE_AGENT_INSTRUCTION,
            "review finished": NodeName.VERIFY_COMPLETION,
        },
    )
    workflow.add_edge(NodeName.FAILURE_INTERRUPT, NodeName.CREATE_AGENT_INSTRUCTION)
    workflow.add_conditional_edges(
        NodeName.VERIFY_COMPLETION,
        route_after_verify_completion,
        {
            "verification finished": NodeName.END_NODE,
            "correction needed": NodeName.CREATE_AGENT_INSTRUCTION,
            "human verification needed": NodeName.COMPLETION_VERIFICATION_INTERRUPT,
            "finalize task branch": NodeName.FINALIZE_TASK_BRANCH,
            "budget exceeded": NodeName.RUN_BUDGET_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.COMPLETION_VERIFICATION_INTERRUPT, NodeName.END_NODE)
    workflow.add_conditional_edges(
        NodeName.FINALIZE_TASK_BRANCH,
        route_after_finalize_task_branch,
        {
            "integration approval": NodeName.INTEGRATION_APPROVAL,
            "integration blocked": NodeName.END_NODE,
        },
    )
    workflow.add_edge(NodeName.INTEGRATION_APPROVAL, NodeName.END_NODE)
    workflow.add_conditional_edges(
        NodeName.RUN_BUDGET_INTERRUPT,
        route_after_run_budget_interrupt,
        {
            "end": NodeName.END_NODE,
            NodeName.READ_REQUEST.value: NodeName.READ_REQUEST,
            NodeName.CHECK_CODE_LOOK_NEED.value: NodeName.CHECK_CODE_LOOK_NEED,
            NodeName.CHECK_PROJECT_GUIDANCE.value: NodeName.CHECK_PROJECT_GUIDANCE,
            NodeName.TECH_LEAD_ANALYSE.value: NodeName.TECH_LEAD_ANALYSE,
            NodeName.CHECK_RESEARCH.value: NodeName.CHECK_RESEARCH,
            NodeName.DISCOVER_RESEARCH_SOURCE.value: NodeName.DISCOVER_RESEARCH_SOURCE,
            NodeName.REVIEW_RISK.value: NodeName.REVIEW_RISK,
            NodeName.REVIEW_PLAN.value: NodeName.REVIEW_PLAN,
            NodeName.APPROVAL_INTERRUPT.value: NodeName.APPROVAL_INTERRUPT,
            NodeName.VERIFY_COMPLETION.value: NodeName.VERIFY_COMPLETION,
        },
    )
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(checkpointer=checkpointer_storage)


graph = build_graph()
