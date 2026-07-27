"""Small LangGraph workflow for producing a safe coding-agent instruction.

This module stays focused on the graph itself so LangGraph Studio can import it
without the local CLI runner getting in the way.
"""

import logging
import operator
from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .app_settings import load_settings
from .coding_agent_runner import CodingAgentCancellationToken, run_coding_agent
from .completion_verifier import CompletionVerificationUnavailable, verify_completion
from .instruction_assembler import build_agent_instruction
from .logging_setup import LOGGER_NAME
from .operator_question import answer_operator_question
from .plan_reviewer import PlanReviewUnavailable, review_plan
from .prompt_loader import PLAN_REQUEST_INSTRUCTION_PROMPT_KEY, render_prompt
from .request_context import resolve_request_context, understand_request
from .research_cache import save_online_source_to_cache
from .research_checker import check_research_requirements
from .research_code_context import collect_code_context, format_code_context_for_prompt
from .research_discovery import discover_official_source
from .research_sources import collect_online_research_sources
from .risk_reviewer import review_task_risk
from .target_project_context import TargetProjectContext
from .tech_lead_analyst import analyse_task

APPROVAL_MAX_REVISION_CYCLES = 5
COMPLETION_VERIFICATION_MAX_CORRECTIONS = 1

logger = logging.getLogger(LOGGER_NAME)

LOG_SEPARATOR = "----------------------------------------"


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "1_read_request"
    UNDERSTAND_AND_BOUND_REQUEST = "1a_understand_and_bound_request"
    RESOLVE_CONTEXT = "1b_resolve_context"
    CHECK_RESEARCH = "1c_check_research"
    DISCOVER_RESEARCH_SOURCE = "1c1_discover_research_source"
    RESEARCH_INTERRUPT = "1c_research_interrupt"
    COLLECT_RESEARCH_EVIDENCE = "1d_collect_research_evidence"
    REVIEW_RISK = "2_review_risk"
    TECH_LEAD_ANALYSE = "3c_tech_lead_analyse"
    APPROVAL_INTERRUPT = "4_approval_interrupt"
    REQUEST_PLAN = "5b_request_plan"
    REVIEW_PLAN = "5c_review_plan"
    PLAN_INTERRUPT = "5d_plan_interrupt"
    CREATE_AGENT_INSTRUCTION = "5_create_agent_instruction"
    RUN_CODING_AGENT = "6_run_coding_agent"
    FAILURE_INTERRUPT = "6b_failure_interrupt"
    VERIFY_COMPLETION = "6c_verify_completion"
    COMPLETION_VERIFICATION_INTERRUPT = "6d_completion_verification_interrupt"
    END_NODE = "7_end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow."""

    request: str
    bounded_request: str
    target_project_context: dict[str, Any] | None
    request_intent: str
    execution_requested: bool
    detected_references: list[str]
    resolved_project_identity: str
    resolved_project_root: str
    resolved_resource_references: list[str]
    unresolved_references: list[str]
    context_resolution_evidence: list[str]
    brief: str
    force_approval: bool
    orchestrator_input_required: bool
    orchestrator_input_reason: str
    orchestrator_input_question: str
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
    online_research_approved: bool
    formulated_task: str
    plan_text: str
    plan_agent_stderr: str
    plan_approved: bool
    plan_review_reason: str
    plan_correction: str
    plan_rejection_count: int
    plan_needs_human_review: bool
    agent_instruction: str
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
    verification_status: str
    verification_reason: str
    verification_correction: str
    verification_attempt_count: int


def build_initial_graph_state(
    request: str,
    *,
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
        "request": request,
        "bounded_request": request,
        "target_project_context": (
            target_project_context.to_payload() if target_project_context is not None else None
        ),
        "request_intent": "",
        "execution_requested": False,
        "detected_references": [],
        "resolved_project_identity": "",
        "resolved_project_root": "",
        "resolved_resource_references": [],
        "unresolved_references": [],
        "context_resolution_evidence": [],
        "brief": "",
        "force_approval": force_approval,
        "orchestrator_input_required": False,
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
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
        "online_research_approved": online_research_approved,
        "formulated_task": "",
        "plan_text": "",
        "plan_agent_stderr": "",
        "plan_approved": False,
        "plan_review_reason": "",
        "plan_correction": "",
        "plan_rejection_count": 0,
        "plan_needs_human_review": False,
        "agent_instruction": "",
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
        "verification_status": "",
        "verification_reason": "",
        "verification_correction": "",
        "verification_attempt_count": 0,
    }


def read_request_node(state: GraphState) -> dict[str, Any]:
    """Validate and normalize the incoming backlog request."""

    _log_node_start("1/7", "READ_REQUEST", "Read backlog request")

    request = state["request"].strip()
    if not request:
        raise ValueError("Request cannot be empty.")

    logger.info("Task: %s", _request_title(request))
    return {"request": request, "restart_required": False}


def understand_and_bound_request_node(state: GraphState) -> dict[str, Any]:
    """Classify intent and detect references before any research decision."""

    _log_node_start("1a", "UNDERSTAND_AND_BOUND_REQUEST", "Understand and bound request")
    understanding = understand_request(state["request"])
    return {
        "request_intent": understanding.intent,
        "execution_requested": understanding.execution_requested,
        "detected_references": list(understanding.detected_references),
    }


def resolve_context_node(state: GraphState) -> dict[str, Any]:
    """Resolve references from explicit supplied context; never infer prefix meaning."""

    _log_node_start("1b", "RESOLVE_CONTEXT", "Resolve project and resource context")
    result = resolve_request_context(
        state["request"],
        target_project_context=_target_project_context_from_state(state),
    )
    question = str(result.pop("clarification_question", "")).strip()
    if question:
        result.update({
            "orchestrator_input_required": True,
            "orchestrator_input_reason": (
                "A referenced project resource could not be resolved safely."
            ),
            "orchestrator_input_question": question,
        })
    return result


def route_after_resolve_context(state: GraphState) -> str:
    if state.get("orchestrator_input_required"):
        return NodeName.END_NODE
    return NodeName.CHECK_RESEARCH


def check_research_node(state: GraphState) -> dict[str, Any]:
    """Identify a knowledge gap, if any, and check research evidence requirements."""

    _log_node_start("1b", "CHECK_RESEARCH", "Check research evidence requirements")
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

    result = check_research_requirements(
        state.get("bounded_request", "") or state["request"],
        settings,
        code_context_root=code_context_root,
    )

    partial: dict[str, Any] = {
        "research_evidence_required": result.has_gap,
        "research_gap_question": result.gap_question,
        "research_source_titles": list(result.usable_source_titles),
        "research_source_locations": list(result.usable_source_locations),
        "research_source_summaries": list(result.usable_source_summaries),
        "online_research_approved": not result.online_research_needed,
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
            f"\"{result.gap_question}\"\n"
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

    _log_node_start("1c1", "DISCOVER_RESEARCH_SOURCE", "Discover a candidate official source")
    settings = load_settings()
    gap_question = str(state.get("research_gap_question", "")).strip()

    candidate = discover_official_source(gap_question, settings)
    if candidate is None:
        logger.info("[LEARN] No discovered source candidate; keeping bounded-registry question.")
        return {}

    question = (
        "I found a candidate official documentation page for this knowledge gap:\n"
        f"\"{gap_question}\"\n"
        f"{candidate.title} — {candidate.url}\n"
        "Do you approve fetching this specific URL? No other URL will be fetched "
        "without separate approval."
    )
    logger.info("[LEARN] Discovered source candidate for approval: %s", candidate.url)
    return {
        "discovered_source_url": candidate.url,
        "discovered_source_title": candidate.title,
        "orchestrator_input_question": question,
    }


def research_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause and ask for human approval before running online research."""

    _log_node_start("1c", "RESEARCH_INTERRUPT", "Pause for human online research approval")
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

    _log_decision_start("1b", "ROUTE_AFTER_CHECK_RESEARCH", "Route after research evidence check")

    if state.get("research_evidence_required") and not state.get("online_research_approved", True):
        logger.info("Decision: research interrupt needed -> DISCOVER_RESEARCH_SOURCE")
        return NodeName.DISCOVER_RESEARCH_SOURCE

    logger.info("Decision: no research interrupt -> REVIEW_RISK")
    return NodeName.REVIEW_RISK


def route_after_research_interrupt(state: GraphState) -> str:
    """Route after the human answers the research approval question."""

    _log_decision_start(
        "1c",
        "ROUTE_AFTER_RESEARCH_INTERRUPT",
        "Route after research approval",
    )

    if state.get("online_research_approved", False):
        logger.info("Decision: research approved -> COLLECT_RESEARCH_EVIDENCE")
        return NodeName.COLLECT_RESEARCH_EVIDENCE

    logger.info("Decision: research rejected -> END_NODE")
    return NodeName.END_NODE


def collect_research_evidence_node(state: GraphState) -> dict[str, Any]:
    """Fetch bounded official docs after the human approves online research."""

    _log_node_start("1d", "COLLECT_RESEARCH_EVIDENCE", "Fetch approved online documentation")
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
    online_sources = collect_online_research_sources(gap_question, settings, extra_urls=extra_urls)
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

    _log_node_start("2/7", "REVIEW_RISK", "Review task risk")
    settings = load_settings()
    logger.info("AI channel: OpenAI Responses API")
    logger.info("AI model: %s", settings.orchestrator_ai_model)
    logger.info("AI enabled: %s", settings.orchestrator_ai_enabled)
    decision = review_task_risk(state.get("bounded_request", "") or state["request"])
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
    }


def tech_lead_analyse_node(state: GraphState) -> dict[str, Any]:
    """Tech lead analysis: formulate the task and produce high-level technical direction."""

    _log_node_start(
        "3c", "TECH_LEAD_ANALYSE", "Tech lead analysis — formulate task and set direction"
    )
    settings = load_settings()

    research_evidence = _format_research_evidence(
        state.get("research_source_titles", []),
        state.get("research_source_locations", []),
        state.get("research_source_summaries", []),
    )

    analysis = analyse_task(
        request=state.get("bounded_request", "") or state["request"],
        task_feedback=list(state.get("task_feedback", [])),
        approval_reason=state.get("approval_reason", ""),
        research_evidence=research_evidence,
        settings=settings,
    )

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
    return {"formulated_task": analysis.task_statement, "brief": analysis.tech_direction}


def route_after_tech_lead_analyse(state: GraphState) -> str:
    """Route after tech lead analysis: approval interrupt if risky, otherwise plan request."""

    _log_decision_start("3c", "ROUTE_AFTER_TECH_LEAD_ANALYSE", "Route after tech lead analysis")

    if state.get("approved"):
        logger.info("Decision: already approved -> REQUEST_PLAN")
        return NodeName.REQUEST_PLAN

    if state["needs_approval"]:
        logger.info("Decision: needs_approval=True -> APPROVAL_INTERRUPT")
        return NodeName.APPROVAL_INTERRUPT

    logger.info("Decision: no approval needed -> REQUEST_PLAN")
    return NodeName.REQUEST_PLAN


def route_after_approval(state: GraphState) -> str:
    """Choose the next LangGraph node name after a human approval decision."""

    _log_decision_start(
        "approval", "ROUTE_AFTER_APPROVAL", "Choose next graph path after human decision"
    )

    action = state.get("approval_action", "approve")

    if action == "cancel":
        logger.info("Decision: action=cancel -> END_NODE")
        return NodeName.END_NODE

    if action == "ask_question":
        logger.info("Decision: action=ask_question -> APPROVAL_INTERRUPT")
        return NodeName.APPROVAL_INTERRUPT

    if action == "request_changes":
        revision_count = int(state.get("approval_revision_count", 0))
        if revision_count >= APPROVAL_MAX_REVISION_CYCLES:
            logger.info(
                "Decision: revision limit reached (%d/%d) -> END_NODE",
                revision_count,
                APPROVAL_MAX_REVISION_CYCLES,
            )
            return NodeName.END_NODE
        logger.info(
            "Decision: action=request_changes (%d/%d) -> TECH_LEAD_ANALYSE",
            revision_count,
            APPROVAL_MAX_REVISION_CYCLES,
        )
        return NodeName.TECH_LEAD_ANALYSE

    logger.info("Decision: action=approve -> REQUEST_PLAN")
    return NodeName.REQUEST_PLAN



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
    instruction = render_prompt(
        PLAN_REQUEST_INSTRUCTION_PROMPT_KEY,
        formulated_task=state.get("formulated_task", "") or state["request"],
        task_feedback=feedback_section,
        correction_feedback=correction_section,
    )
    target_project_root = _target_project_root(state, settings)
    result = run_coding_agent(
        agent_instruction=instruction,
        project_root=target_project_root,
        settings=settings,
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
        "plan_approved": False,
        "plan_correction": "",
    }


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
    formulated_task = state.get("formulated_task", "") or state["request"]
    rejection_count = state.get("plan_rejection_count", 0)

    logger.info("Reviewing plan (rejection_count=%d):", rejection_count)
    logger.info("Plan text:\n%s", plan_text or "<empty>")

    try:
        decision = review_plan(
            formulated_task=formulated_task,
            plan_text=plan_text,
            agent_error=plan_agent_stderr,
            settings=settings,
        )
    except PlanReviewUnavailable as exc:
        logger.warning("Plan reviewer unavailable: %s — routing to human interrupt.", exc)
        return {
            "plan_approved": False,
            "plan_review_reason": str(exc),
            "plan_correction": "",
            "plan_needs_human_review": True,
        }

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
        }

    logger.info("Plan approved.")
    if progress_callback is not None:
        progress_callback("Plan approved. Starting implementation...")
    return {
        "plan_approved": True,
        "plan_review_reason": decision.reason,
        "plan_correction": "",
        "plan_needs_human_review": False,
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

    if state.get("plan_needs_human_review"):
        logger.info("Decision: plan reviewer unavailable -> PLAN_INTERRUPT")
        return NodeName.PLAN_INTERRUPT

    if state["plan_approved"]:
        logger.info("Decision: plan approved -> CREATE_AGENT_INSTRUCTION")
        return NodeName.CREATE_AGENT_INSTRUCTION

    rejection_count = state.get("plan_rejection_count", 0)
    if rejection_count >= 2:
        logger.info("Decision: plan rejected %d times -> PLAN_INTERRUPT", rejection_count)
        return NodeName.PLAN_INTERRUPT

    logger.info(
        "Decision: plan rejected (%d/2), sending correction -> REQUEST_PLAN",
        rejection_count,
    )
    return NodeName.REQUEST_PLAN


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
        return {
            "approved": True,
            "approved_by": approved_by,
            "approval_action": "approve",
            "approval_last_question": "",
            "approval_last_answer": "",
        }

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
    answer = answer_operator_question(
        question=question,
        request=request_text,
        target_project_context=context,
        code_context=code_context_text,
        research_evidence=research_evidence,
        settings=settings,
    )
    logger.info("Approval interrupt resumed: action=ask_question")
    return {
        "approval_action": "ask_question",
        "approval_last_question": question,
        "approval_last_answer": answer,
    }


def create_agent_instruction_node(state: GraphState) -> dict[str, Any]:
    """Create the bounded instruction package for the coding-agent process."""

    _log_node_start("5/7", "CREATE_AGENT_INSTRUCTION", "Create coding-agent instruction")
    settings = load_settings()
    correction = (
        state.get("coding_agent_correction", "").strip()
        or state.get("verification_correction", "").strip()
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
    )

    logger.info(
        "Instruction purpose: merge task, brief, project rules, selected skills, "
        "allowed directories, stop conditions, and validation expectations."
    )
    logger.info("Instruction research evidence items: %d", len(research_evidence))
    logger.info("Instruction size: %s characters", len(agent_instruction))
    logger.info("Instruction preview: %s", _single_line_preview(agent_instruction, limit=360))

    return {"agent_instruction": agent_instruction}


def _log_node_start(step: str, node_name: str, description: str) -> None:
    logger.info(LOG_SEPARATOR)
    logger.info("NODE [%s] %s - %s", step, node_name, description)


def _log_decision_start(step: str, decision_name: str, description: str) -> None:
    logger.info(LOG_SEPARATOR)
    logger.info("DECISION [%s] %s - %s", step, decision_name, description)


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


def _coding_agent_result_succeeded(result: Any) -> bool:
    """Return success for a coding-agent result with a safe fallback.

    Prefer an explicit ``success`` field when the adapter provides one. When it
    does not, infer success from a zero return code as long as the run did not
    time out or get cancelled.
    """

    explicit_success = getattr(result, "success", None)
    if explicit_success is not None:
        return bool(explicit_success)
    if getattr(result, "timed_out", False) or getattr(result, "cancelled", False):
        return False
    return getattr(result, "returncode", None) == 0


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
    logger.info("Coding agent command: %s", settings.coding_agent_command)
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
        "project_root": target_project_root,
        "settings": settings,
        "progress_callback": progress_callback,
        "use_pty": True,
        "sandbox_override": "workspace-write",
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

    changed_files = getattr(result, "changed_files_delta", ())
    restart_required = _restart_required_for_changed_files(changed_files)
    success = _coding_agent_result_succeeded(result)
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

    return {
        "coding_agent_result": result.summary(),
        "coding_agent_success": success,
        "coding_agent_changed_files": changed_files,
        "restart_required": restart_required,
        "coding_agent_retry_count": new_retry_count,
        "coding_agent_correction": correction,
        "coding_agent_command": command[0] if command else "",
        "coding_agent_returncode": getattr(result, "returncode", None),
        "coding_agent_timed_out": getattr(result, "timed_out", False),
        "coding_agent_performed_by": settings.coding_agent_command if command else "",
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

    if state.get("coding_agent_success"):
        logger.info("Decision: success -> VERIFY_COMPLETION")
        return NodeName.VERIFY_COMPLETION

    retry_count = state.get("coding_agent_retry_count", 0)
    if retry_count < 2:
        logger.info("Decision: failure retry_count=%d < 2 -> CREATE_AGENT_INSTRUCTION", retry_count)
        return NodeName.CREATE_AGENT_INSTRUCTION

    logger.info("Decision: failure retry_count=%d >= 2 -> FAILURE_INTERRUPT", retry_count)
    return NodeName.FAILURE_INTERRUPT


def verify_completion_node(state: GraphState) -> dict[str, Any]:
    """AI Tech Lead verifies the coding agent's completed work against the approved task."""

    _log_node_start("6c", "VERIFY_COMPLETION", "Verify completed work against the approved task")
    settings = load_settings()
    attempt_count = state.get("verification_attempt_count", 0)
    prior_correction = state.get("verification_correction", "")
    target_project_context = _target_project_context_from_state(state)

    try:
        decision = verify_completion(
            bounded_request=state.get("bounded_request", "") or state["request"],
            formulated_task=state.get("formulated_task", ""),
            brief=state.get("brief", ""),
            plan_text=state.get("plan_text", ""),
            acceptance_criteria=settings.acceptance_criteria,
            changed_files=state.get("coding_agent_changed_files", ()),
            coding_agent_result=state.get("coding_agent_result", ""),
            target_project_context=target_project_context,
            settings=settings,
            prior_correction=prior_correction,
        )
    except CompletionVerificationUnavailable as error:
        logger.warning("Completion verification unavailable: %s", error)
        return {
            "verification_status": "human_verification_required",
            "verification_reason": f"Verification unavailable: {error}",
            "verification_correction": "",
        }

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
        logger.info(
            "Decision: correction limit reached (%d) -> failed", COMPLETION_VERIFICATION_MAX_CORRECTIONS
        )
        return {
            "verification_status": "failed",
            "verification_reason": decision.reason,
            "verification_correction": "",
        }

    updates: dict[str, Any] = {
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

    rejection_text = str(result.get("text", "")).strip() if isinstance(result, dict) else str(result)
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
    status = state.get("verification_status", "")

    if status == "human_verification_required":
        logger.info("Decision: human_verification_required -> COMPLETION_VERIFICATION_INTERRUPT")
        return NodeName.COMPLETION_VERIFICATION_INTERRUPT

    if status == "correction_required":
        logger.info("Decision: correction_required -> CREATE_AGENT_INSTRUCTION")
        return NodeName.CREATE_AGENT_INSTRUCTION

    logger.info("Decision: %s -> END_NODE", status or "complete")
    return NodeName.END_NODE


def end_node(state: GraphState) -> dict[str, Any]:
    """Log the final request and approval status for review."""

    _log_node_start("7/7", "END_NODE", "Workflow complete")
    logger.info("Task: %s", _request_title(state["request"]))
    logger.info("Approval: required=%s approved=%s", state["needs_approval"], state["approved"])
    logger.info("Approved by: %s", state.get("approved_by", "") or "not required")
    logger.info("Performed by: %s", state.get("coding_agent_performed_by", "") or "not run")


def _target_project_context_from_state(state: GraphState) -> TargetProjectContext | None:
    payload = state.get("target_project_context")
    return TargetProjectContext.from_payload(payload if isinstance(payload, dict) else None)
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
    logger.info("---END---")
    return {"restart_required": state.get("restart_required", False)}


def build_graph(
    checkpointer_storage=None,
    execute_coding_agent_override: bool | None = None,
    project_root_override: str | None = None,
    coding_agent_progress_callback: Callable[[str], None] | None = None,
    coding_agent_cancellation_token: CodingAgentCancellationToken | None = None,
):
    """Build and compile the backlog-to-agent-instruction workflow."""

    workflow = StateGraph(GraphState)

    workflow.add_node(NodeName.READ_REQUEST, read_request_node)
    workflow.add_node(NodeName.UNDERSTAND_AND_BOUND_REQUEST, understand_and_bound_request_node)
    workflow.add_node(NodeName.RESOLVE_CONTEXT, resolve_context_node)
    workflow.add_node(NodeName.CHECK_RESEARCH, check_research_node)
    workflow.add_node(NodeName.DISCOVER_RESEARCH_SOURCE, discover_research_source_node)
    workflow.add_node(NodeName.RESEARCH_INTERRUPT, research_interrupt_node)
    workflow.add_node(NodeName.COLLECT_RESEARCH_EVIDENCE, collect_research_evidence_node)
    workflow.add_node(NodeName.REVIEW_RISK, review_risk_node)
    workflow.add_node(NodeName.TECH_LEAD_ANALYSE, tech_lead_analyse_node)
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
    workflow.add_node(NodeName.VERIFY_COMPLETION, verify_completion_node)
    workflow.add_node(
        NodeName.COMPLETION_VERIFICATION_INTERRUPT, completion_verification_interrupt_node
    )
    workflow.add_node(NodeName.END_NODE, end_node)

    workflow.add_edge(START, NodeName.READ_REQUEST)
    workflow.add_edge(NodeName.READ_REQUEST, NodeName.UNDERSTAND_AND_BOUND_REQUEST)
    workflow.add_edge(NodeName.UNDERSTAND_AND_BOUND_REQUEST, NodeName.RESOLVE_CONTEXT)
    workflow.add_conditional_edges(
        NodeName.RESOLVE_CONTEXT,
        route_after_resolve_context,
        {
            NodeName.CHECK_RESEARCH: NodeName.CHECK_RESEARCH,
            NodeName.END_NODE: NodeName.END_NODE,
        },
    )
    workflow.add_conditional_edges(
        NodeName.CHECK_RESEARCH,
        route_after_check_research,
        {
            NodeName.DISCOVER_RESEARCH_SOURCE: NodeName.DISCOVER_RESEARCH_SOURCE,
            NodeName.REVIEW_RISK: NodeName.REVIEW_RISK,
        },
    )
    workflow.add_edge(NodeName.DISCOVER_RESEARCH_SOURCE, NodeName.RESEARCH_INTERRUPT)
    workflow.add_conditional_edges(
        NodeName.RESEARCH_INTERRUPT,
        route_after_research_interrupt,
        {
            NodeName.COLLECT_RESEARCH_EVIDENCE: NodeName.COLLECT_RESEARCH_EVIDENCE,
            NodeName.END_NODE: NodeName.END_NODE,
        },
    )
    workflow.add_edge(NodeName.COLLECT_RESEARCH_EVIDENCE, NodeName.REVIEW_RISK)
    workflow.add_edge(NodeName.REVIEW_RISK, NodeName.TECH_LEAD_ANALYSE)
    workflow.add_conditional_edges(
        NodeName.TECH_LEAD_ANALYSE,
        route_after_tech_lead_analyse,
        {
            NodeName.APPROVAL_INTERRUPT: NodeName.APPROVAL_INTERRUPT,
            NodeName.REQUEST_PLAN: NodeName.REQUEST_PLAN,
        },
    )
    workflow.add_conditional_edges(
        NodeName.APPROVAL_INTERRUPT,
        route_after_approval,
        {
            NodeName.REQUEST_PLAN: NodeName.REQUEST_PLAN,
            NodeName.TECH_LEAD_ANALYSE: NodeName.TECH_LEAD_ANALYSE,
            NodeName.APPROVAL_INTERRUPT: NodeName.APPROVAL_INTERRUPT,
            NodeName.END_NODE: NodeName.END_NODE,
        },
    )
    workflow.add_edge(NodeName.REQUEST_PLAN, NodeName.REVIEW_PLAN)
    workflow.add_conditional_edges(
        NodeName.REVIEW_PLAN,
        route_after_review_plan,
        {
            NodeName.CREATE_AGENT_INSTRUCTION: NodeName.CREATE_AGENT_INSTRUCTION,
            NodeName.REQUEST_PLAN: NodeName.REQUEST_PLAN,
            NodeName.PLAN_INTERRUPT: NodeName.PLAN_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.PLAN_INTERRUPT, NodeName.REQUEST_PLAN)

    workflow.add_edge(NodeName.CREATE_AGENT_INSTRUCTION, NodeName.RUN_CODING_AGENT)
    workflow.add_conditional_edges(
        NodeName.RUN_CODING_AGENT,
        route_after_run_coding_agent,
        {
            NodeName.VERIFY_COMPLETION: NodeName.VERIFY_COMPLETION,
            NodeName.CREATE_AGENT_INSTRUCTION: NodeName.CREATE_AGENT_INSTRUCTION,
            NodeName.FAILURE_INTERRUPT: NodeName.FAILURE_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.FAILURE_INTERRUPT, NodeName.CREATE_AGENT_INSTRUCTION)
    workflow.add_conditional_edges(
        NodeName.VERIFY_COMPLETION,
        route_after_verify_completion,
        {
            NodeName.END_NODE: NodeName.END_NODE,
            NodeName.CREATE_AGENT_INSTRUCTION: NodeName.CREATE_AGENT_INSTRUCTION,
            NodeName.COMPLETION_VERIFICATION_INTERRUPT: NodeName.COMPLETION_VERIFICATION_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.COMPLETION_VERIFICATION_INTERRUPT, NodeName.END_NODE)
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(checkpointer=checkpointer_storage)


graph = build_graph()
