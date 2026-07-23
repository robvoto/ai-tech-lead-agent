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
from .instruction_assembler import build_agent_instruction
from .logging_setup import LOGGER_NAME
from .plan_reviewer import PlanReviewUnavailable, review_plan
from .prompt_loader import PLAN_REQUEST_INSTRUCTION_PROMPT_KEY, render_prompt
from .research_cache import save_online_source_to_cache
from .research_checker import check_research_requirements
from .research_sources import collect_online_research_sources
from .request_context import resolve_request_context, understand_request
from .risk_reviewer import review_task_risk
from .tech_lead_analyst import analyse_task

logger = logging.getLogger(LOGGER_NAME)

LOG_SEPARATOR = "----------------------------------------"


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "1_read_request"
    UNDERSTAND_AND_BOUND_REQUEST = "1a_understand_and_bound_request"
    RESOLVE_CONTEXT = "1b_resolve_context"
    CHECK_RESEARCH = "1c_check_research"
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
    END_NODE = "7_end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow."""

    request: str
    bounded_request: str
    supplied_context: dict[str, Any]
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
    research_evidence_required: bool
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


def build_initial_graph_state(
    request: str,
    *,
    force_approval: bool = False,
    approved: bool = False,
    approved_by: str = "",
    approval_reason: str = "Risk review has not run yet.",
    online_research_approved: bool = False,
    task_feedback: list[str] | None = None,
    supplied_context: dict[str, Any] | None = None,
) -> GraphState:
    """Return one canonical initial state for the coding workflow graph."""

    return {
        "request": request,
        "bounded_request": request,
        "supplied_context": dict(supplied_context or {}),
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
        "research_evidence_required": False,
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
        supplied_context=dict(state.get("supplied_context", {})),
    )
    question = str(result.pop("clarification_question", "")).strip()
    if question:
        result.update({
            "orchestrator_input_required": True,
            "orchestrator_input_reason": "A referenced project resource could not be resolved safely.",
            "orchestrator_input_question": question,
        })
    return result


def route_after_resolve_context(state: GraphState) -> str:
    if state.get("orchestrator_input_required"):
        return NodeName.END_NODE
    return NodeName.CHECK_RESEARCH


def check_research_node(state: GraphState) -> dict[str, Any]:
    """Check research evidence requirements for complex tasks."""

    _log_node_start("1b", "CHECK_RESEARCH", "Check research evidence requirements")
    settings = load_settings()

    result = check_research_requirements(state.get("bounded_request", "") or state["request"], settings)

    partial: dict[str, Any] = {
        "research_evidence_required": result.is_complex,
        "research_source_titles": list(result.usable_source_titles),
        "research_source_locations": list(result.usable_source_locations),
        "research_source_summaries": list(result.usable_source_summaries),
        "online_research_approved": not result.online_research_needed,
    }

    if result.online_research_needed:
        question = (
            f"Local docs found {result.sources_found} relevant source(s) "
            f"(minimum {settings.research_min_local_sources} required) for this complex task.\n"
            f"Reason: {result.complexity_reason}\n"
            "Do you want me to fetch the approved LangChain docs next?"
        )
        partial["orchestrator_input_required"] = True
        partial["orchestrator_input_reason"] = result.complexity_reason
        partial["orchestrator_input_question"] = question
        logger.info(
            "[LEARN] Research interrupt required: sources_found=%d reason=%s",
            result.sources_found,
            result.complexity_reason,
        )
    else:
        if result.is_complex:
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
        logger.info("Decision: research interrupt needed -> RESEARCH_INTERRUPT")
        return NodeName.RESEARCH_INTERRUPT

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

    online_sources = collect_online_research_sources(state.get("bounded_request", "") or state["request"], settings)
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
    """Choose the next LangGraph node name after human approval is received."""

    _log_decision_start(
        "approval", "ROUTE_AFTER_APPROVAL", "Choose next graph path after human decision"
    )

    if state["approved"]:
        logger.info("Decision: approved=True -> REQUEST_PLAN")
        return NodeName.REQUEST_PLAN

    logger.info("Decision: approved=False -> END_NODE")
    return NodeName.END_NODE


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
    result = run_coding_agent(
        agent_instruction=instruction,
        project_root=Path(settings.project_root),
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


def approval_interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause for human approval before creating a coding-agent instruction."""

    _log_node_start("4/7", "APPROVAL_INTERRUPT", "Pause for human approval")
    approval_reason = str(state.get("approval_reason", "")).strip()
    formulated_task = str(state.get("formulated_task", "")).strip()
    logger.info("Approval interrupt: reason=%s", approval_reason)
    result = interrupt(
        {"kind": "approval", "reason": approval_reason, "formulated_task": formulated_task}
    )
    approved = bool(result.get("approved", False)) if isinstance(result, dict) else bool(result)
    approved_by = str(result.get("approved_by", "")) if isinstance(result, dict) else ""
    logger.info("Approval interrupt resumed: approved=%s approved_by=%s", approved, approved_by)
    return {"approved": approved, "approved_by": approved_by}


def create_agent_instruction_node(state: GraphState) -> dict[str, Any]:
    """Create the bounded instruction package for the coding-agent process."""

    _log_node_start("5/7", "CREATE_AGENT_INSTRUCTION", "Create coding-agent instruction")
    settings = load_settings()
    correction = state.get("coding_agent_correction", "").strip() or None
    if correction:
        logger.info(
            "Including failure correction in instruction (retry_count=%d)",
            state.get("coding_agent_retry_count", 0),
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
        project_root=Path(settings.project_root),
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
    if project_root_override is not None:
        settings = replace(settings, project_root=project_root_override)

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
    logger.info("Working directory: %s", settings.project_root)

    if settings.execute_coding_agent:
        logger.info("[LEARN] Starting coding agent subprocess now. This may take several minutes.")
        if progress_callback is not None:
            progress_callback(
                f"Running coding agent ({settings.coding_agent_command}). "
                "This may take several minutes..."
            )

    runner_kwargs = {
        "agent_instruction": state["agent_instruction"],
        "project_root": Path(settings.project_root),
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
            settings.project_root,
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
        logger.info("Decision: success -> END_NODE")
        return NodeName.END_NODE

    retry_count = state.get("coding_agent_retry_count", 0)
    if retry_count < 2:
        logger.info("Decision: failure retry_count=%d < 2 -> CREATE_AGENT_INSTRUCTION", retry_count)
        return NodeName.CREATE_AGENT_INSTRUCTION

    logger.info("Decision: failure retry_count=%d >= 2 -> FAILURE_INTERRUPT", retry_count)
    return NodeName.FAILURE_INTERRUPT


def end_node(state: GraphState) -> dict[str, Any]:
    """Log the final request and approval status for review."""

    _log_node_start("7/7", "END_NODE", "Workflow complete")
    logger.info("Task: %s", _request_title(state["request"]))
    logger.info("Approval: required=%s approved=%s", state["needs_approval"], state["approved"])
    logger.info("Approved by: %s", state.get("approved_by", "") or "not required")
    logger.info("Performed by: %s", state.get("coding_agent_performed_by", "") or "not run")
    logger.info("Retries: %s", state.get("coding_agent_retry_count", 0))
    coding_agent_result = state.get("coding_agent_result", "").strip()
    if coding_agent_result:
        logger.info("Coding agent: %s", coding_agent_result.splitlines()[0])
    else:
        logger.info("Coding agent: not run")
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
            NodeName.RESEARCH_INTERRUPT: NodeName.RESEARCH_INTERRUPT,
            NodeName.REVIEW_RISK: NodeName.REVIEW_RISK,
        },
    )
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
            NodeName.END_NODE: NodeName.END_NODE,
            NodeName.CREATE_AGENT_INSTRUCTION: NodeName.CREATE_AGENT_INSTRUCTION,
            NodeName.FAILURE_INTERRUPT: NodeName.FAILURE_INTERRUPT,
        },
    )
    workflow.add_edge(NodeName.FAILURE_INTERRUPT, NodeName.CREATE_AGENT_INSTRUCTION)
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(checkpointer=checkpointer_storage)


graph = build_graph()
