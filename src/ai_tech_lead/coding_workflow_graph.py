"""Small LangGraph workflow for producing a safe coding-agent instruction.

This module stays focused on the graph itself so LangGraph Studio can import it
without the local CLI runner getting in the way.
"""

import logging
from dataclasses import replace
from enum import StrEnum
from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .app_settings import load_settings
from .clarification_checker import check_task_clarification
from .prompt_loader import load_prompt
from .coding_agent_runner import run_coding_agent
from .plan_reviewer import PlanReviewUnavailable, review_plan
from .research_checker import check_research_requirements
from .task_formulator import formulate_task
from .config import PROJECT_ROOT
from .instruction_assembler import build_agent_instruction
from .logging_setup import LOGGER_NAME
from .risk_reviewer import review_task_risk

logger = logging.getLogger(LOGGER_NAME)

LOG_SEPARATOR = "----------------------------------------"


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "1_read_request"
    CHECK_RESEARCH = "1b_check_research"
    RESEARCH_GATE = "1c_research_gate"
    REVIEW_RISK = "2_review_risk"
    CREATE_BRIEF = "3_create_brief"
    CHECK_CLARIFICATION = "3b_check_clarification"
    CLARIFICATION_GATE = "3c_clarification_gate"
    FORMULATE_TASK = "3d_formulate_task"
    APPROVAL_REQUIRED = "4_approval_required"
    REQUEST_PLAN = "5b_request_plan"
    REVIEW_PLAN = "5c_review_plan"
    PLAN_HUMAN_GATE = "5d_plan_human_gate"
    CREATE_AGENT_INSTRUCTION = "5_create_agent_instruction"
    RUN_CODING_AGENT = "6_run_coding_agent"
    END_NODE = "7_end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow."""

    request: str
    brief: str
    force_approval: bool
    # Reserved for future conditional interrupt/resume handling.
    orchestrator_input_required: bool
    orchestrator_input_kind: str
    orchestrator_input_reason: str
    orchestrator_input_question: str
    orchestrator_input_source_node: str
    task_feedback: list[str]
    needs_approval: bool
    approval_reason: str
    approved: bool
    approved_by: str
    research_evidence_required: bool
    research_sources_found: int
    research_source_titles: list[str]
    online_research_approved: bool
    formulated_task: str
    plan_text: str
    plan_approved: bool
    plan_review_reason: str
    plan_correction: str
    plan_rejection_count: int
    plan_needs_human_review: bool
    agent_instruction: str
    coding_agent_result: str
    coding_agent_success: bool
    coding_agent_changed_files: tuple[str, ...]
    coding_agent_command: str
    coding_agent_returncode: int | None
    coding_agent_timed_out: bool
    coding_agent_performed_by: str


def read_request_node(state: GraphState) -> GraphState:
    """Validate and normalize the incoming backlog request."""

    _log_node_start("1/7", "READ_REQUEST", "Read backlog request")

    request = state["request"].strip()
    if not request:
        raise ValueError("Request cannot be empty.")

    state["request"] = request
    logger.info("Task: %s", _request_title(state["request"]))
    return state


def check_research_node(state: GraphState) -> GraphState:
    """Check research evidence requirements for complex tasks."""

    _log_node_start("1b", "CHECK_RESEARCH", "Check research evidence requirements")
    settings = load_settings()

    result = check_research_requirements(state["request"], settings)

    state["research_evidence_required"] = result.is_complex
    state["research_sources_found"] = result.sources_found
    state["research_source_titles"] = list(result.usable_source_titles)
    state["online_research_approved"] = not result.online_research_needed

    if result.online_research_needed:
        state["orchestrator_input_required"] = True
        state["orchestrator_input_kind"] = "online_research_needed"
        state["orchestrator_input_question"] = (
            f"Local research cache has {result.sources_found} usable source(s) "
            f"(minimum 2 required) for this complex task.\n"
            f"Reason: {result.complexity_reason}\n"
            "Do you want me to research online before continuing?"
        )
        logger.info(
            "[LEARN] Research gate required: sources_found=%d reason=%s",
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

    return state


def research_gate_node(state: GraphState) -> GraphState:
    """Pass-through gate interrupted to wait for human online research approval."""

    _log_node_start("1c", "RESEARCH_GATE", "Resume after human research approval decision")
    logger.info(
        "[LEARN] Resuming: online_research_approved=%s",
        state.get("online_research_approved", False),
    )
    return state


def route_after_check_research(state: GraphState) -> str:
    """Route after research check: gate if complex task lacks local sources, else continue."""

    _log_decision_start("1b", "ROUTE_AFTER_CHECK_RESEARCH", "Route after research evidence check")

    if state.get("research_evidence_required") and not state.get("online_research_approved", True):
        logger.info("Decision: research gate needed -> RESEARCH_GATE")
        return NodeName.RESEARCH_GATE

    logger.info("Decision: no research gate -> REVIEW_RISK")
    return NodeName.REVIEW_RISK


def review_risk_node(state: GraphState) -> GraphState:
    """Decide whether this task needs human approval.

    The graph owns this decision now. The backlog may describe the task, but it
    no longer decides whether approval is required.
    """

    _log_node_start("2/7", "REVIEW_RISK", "Review task risk")
    settings = load_settings()
    logger.info("AI channel: OpenAI Responses API")
    logger.info("AI model: %s", settings.orchestrator_ai_model)
    logger.info("AI enabled: %s", settings.orchestrator_ai_enabled)
    decision = review_task_risk(state["request"])
    state["needs_approval"] = decision.needs_approval
    state["approval_reason"] = decision.approval_reason

    if state.get("force_approval"):
        state["needs_approval"] = True
        forced_note = "Interrupt Before Implementation flag set in backlog item."
        state["approval_reason"] = f"{forced_note} {state['approval_reason']}".strip()
        logger.info("[LEARN] Force approval: Interrupt Before Implementation flag is set — overriding to needs_approval=True.")

    logger.info(
        "Approval: %s (%s)",
        "required" if state["needs_approval"] else "not required",
        state["approval_reason"],
    )
    return state


def create_brief_node(state: GraphState) -> GraphState:
    """Create a structured execution brief for the selected backlog task."""

    _log_node_start("3/7", "CREATE_BRIEF", "Create execution brief")

    settings = load_settings()
    state["brief"] = settings.prompts["execution_brief_template"].format(
        request=state["request"],
        relevant_files=_format_bullets(settings.watched_directories),
        constraint_list=_format_bullets(settings.brief_constraints),
        acceptance_criteria=_format_bullets(settings.acceptance_criteria),
        approval_reason=state["approval_reason"],
        risk_notes=_format_bullets(settings.risk_notes),
    )

    logger.info("Brief purpose: convert request plus settings into the structured context used by the final agent handoff.")
    logger.info("Brief changed instruction: adds watched directories=%s, constraints=%s, acceptance criteria=%s", len(settings.watched_directories), len(settings.brief_constraints), len(settings.acceptance_criteria))
    logger.info("Brief size: %s characters", len(state["brief"]))
    logger.info("Brief preview: %s", _single_line_preview(state["brief"]))

    return state


def check_clarification_node(state: GraphState) -> GraphState:
    """Ask the orchestrator LLM if clarification is needed before writing the coding agent instruction."""

    _log_node_start("3b", "CHECK_CLARIFICATION", "Check if clarification is needed")
    settings = load_settings()
    logger.info("Feedback items so far: %d", len(state.get("task_feedback", [])))

    decision = check_task_clarification(
        request=state["request"],
        brief=state["brief"],
        task_feedback=list(state.get("task_feedback", [])),
        settings=settings,
    )

    state["orchestrator_input_required"] = decision.needs_clarification
    state["orchestrator_input_question"] = decision.question
    state["orchestrator_input_reason"] = decision.reason

    if decision.needs_clarification:
        logger.info("Clarification needed: %s", decision.question)
    else:
        logger.info("Clarification not needed, proceeding.")

    return state


def clarification_gate_node(state: GraphState) -> GraphState:
    """Pass-through gate interrupted before execution to wait for human clarification reply."""

    _log_node_start("3c", "CLARIFICATION_GATE", "Resume after human clarification")
    logger.info("Resuming with %d feedback item(s).", len(state.get("task_feedback", [])))
    return state


def formulate_task_node(state: GraphState) -> GraphState:
    """Ask the orchestrator LLM to write a clean, unambiguous task description for the coding agent."""

    _log_node_start("3d", "FORMULATE_TASK", "Formulate coding-agent task description")
    settings = load_settings()

    result = formulate_task(
        request=state["request"],
        brief=state["brief"],
        task_feedback=list(state.get("task_feedback", [])),
        settings=settings,
    )

    state["formulated_task"] = result.task_description
    logger.info("Formulated task (%d chars): %s", len(result.task_description), _single_line_preview(result.task_description))
    return state


def route_after_check_clarification(state: GraphState) -> str:
    """Route after the clarification check: loop back if more info needed, otherwise formulate task."""

    _log_decision_start("3b", "ROUTE_AFTER_CHECK_CLARIFICATION", "Route after clarification check")

    if state["orchestrator_input_required"]:
        logger.info("Decision: clarification needed -> CLARIFICATION_GATE")
        return NodeName.CLARIFICATION_GATE

    logger.info("Decision: clarification satisfied -> FORMULATE_TASK")
    return NodeName.FORMULATE_TASK


def route_after_formulate_task(state: GraphState) -> str:
    """Route after task formulation: approval gate if risky, otherwise plan request."""

    _log_decision_start("3d", "ROUTE_AFTER_FORMULATE_TASK", "Route after task formulation")

    if state["needs_approval"]:
        logger.info("Decision: needs_approval=True -> APPROVAL_REQUIRED")
        return NodeName.APPROVAL_REQUIRED

    logger.info("Decision: no approval needed -> REQUEST_PLAN")
    return NodeName.REQUEST_PLAN


def route_after_approval(state: GraphState) -> str:
    """Choose the next LangGraph node name after human approval is received."""

    _log_decision_start("approval", "ROUTE_AFTER_APPROVAL", "Choose next graph path after human decision")

    if state["approved"]:
        logger.info("Decision: approved=True -> REQUEST_PLAN")
        return NodeName.REQUEST_PLAN

    logger.info("Decision: approved=False -> END_NODE")
    return NodeName.END_NODE


def request_plan_node(state: GraphState) -> GraphState:
    """Run the coding agent in planning mode to get a high-level implementation plan."""

    _log_node_start("5b", "REQUEST_PLAN", "Request implementation plan from coding agent")
    settings = load_settings()

    correction = state.get("plan_correction", "").strip()
    feedback = list(state.get("task_feedback", []))
    rejection_count = state.get("plan_rejection_count", 0)

    logger.info("Plan request: rejection_count=%d feedback_items=%d", rejection_count, len(feedback))
    if correction:
        logger.info("Plan correction feedback: %s", correction)
    if feedback:
        logger.info("Task feedback included: %s", " | ".join(feedback))

    plan_prompt = load_prompt("plan_request_instruction.md")
    correction_section = f"\nPrevious plan was rejected. Correction needed:\n{correction}" if correction else ""
    instruction = plan_prompt.replace("{formulated_task}", state.get("formulated_task", "") or state["request"]).replace("{correction_feedback}", correction_section)

    result = run_coding_agent(
        agent_instruction=instruction,
        project_root=PROJECT_ROOT,
        settings=settings,
    )

    plan_text = result.stdout.strip()
    logger.info("Plan received (%d chars): %s", len(plan_text), _single_line_preview(plan_text, limit=360))
    logger.info("Plan full text:\n%s", plan_text or "<empty>")
    logger.info("Plan request exit code: %s", result.returncode)
    if result.changed_files_delta:
        logger.warning("Plan request unexpectedly changed files: %s", result.changed_files_delta)

    state["plan_text"] = plan_text
    state["plan_approved"] = False
    state["plan_correction"] = ""
    return state


def review_plan_node(state: GraphState) -> GraphState:
    """Ask the orchestrator LLM whether the coding agent's plan correctly addresses the task."""

    _log_node_start("5c", "REVIEW_PLAN", "Review coding agent plan")
    settings = load_settings()

    plan_text = state.get("plan_text", "").strip()
    formulated_task = state.get("formulated_task", "") or state["request"]
    rejection_count = state.get("plan_rejection_count", 0)

    logger.info("Reviewing plan (rejection_count=%d):", rejection_count)
    logger.info("Plan text:\n%s", plan_text or "<empty>")

    try:
        decision = review_plan(
            formulated_task=formulated_task,
            plan_text=plan_text,
            settings=settings,
        )
    except PlanReviewUnavailable as exc:
        logger.warning("Plan reviewer unavailable: %s — routing to human gate.", exc)
        state["plan_approved"] = False
        state["plan_review_reason"] = str(exc)
        state["plan_correction"] = ""
        state["plan_needs_human_review"] = True
        return state

    logger.info("Plan review decision: approved=%s reason=%s", decision.approved, decision.reason)
    if not decision.approved:
        logger.info("Plan correction guidance: %s", decision.correction)

    state["plan_approved"] = decision.approved
    state["plan_review_reason"] = decision.reason
    state["plan_correction"] = decision.correction if not decision.approved else ""
    state["plan_needs_human_review"] = False

    if not decision.approved:
        state["plan_rejection_count"] = rejection_count + 1
        logger.info("Plan rejection count now: %d", state["plan_rejection_count"])

    return state


def plan_human_gate_node(state: GraphState) -> GraphState:
    """Pass-through gate interrupted before execution to ask the human for plan guidance."""

    _log_node_start("5d", "PLAN_HUMAN_GATE", "Resume after human plan guidance")
    feedback = list(state.get("task_feedback", []))
    logger.info("Human guidance received (%d feedback items). Resetting rejection count.", len(feedback))
    state["plan_rejection_count"] = 0
    state["plan_correction"] = ""
    return state


def route_after_review_plan(state: GraphState) -> str:
    """Route after plan review: proceed, loop with correction, or ask human."""

    _log_decision_start("5c", "ROUTE_AFTER_REVIEW_PLAN", "Route after plan review")

    if state.get("plan_needs_human_review"):
        logger.info("Decision: plan reviewer unavailable -> PLAN_HUMAN_GATE")
        return NodeName.PLAN_HUMAN_GATE

    if state["plan_approved"]:
        logger.info("Decision: plan approved -> CREATE_AGENT_INSTRUCTION")
        return NodeName.CREATE_AGENT_INSTRUCTION

    rejection_count = state.get("plan_rejection_count", 0)
    if rejection_count >= 2:
        logger.info("Decision: plan rejected %d times -> PLAN_HUMAN_GATE", rejection_count)
        return NodeName.PLAN_HUMAN_GATE

    logger.info("Decision: plan rejected (%d/2), sending correction -> REQUEST_PLAN", rejection_count)
    return NodeName.REQUEST_PLAN


def approval_required_node(state: GraphState) -> GraphState:
    """Human approval gate before creating a coding-agent instruction."""

    _log_node_start("4/7", "APPROVAL_REQUIRED", "Pause for human approval")
    logger.info("Approval pause reached")
    logger.info("Approval reason: %s", state["approval_reason"])
    logger.info("This request should not continue without human approval.")

    return state


def create_agent_instruction_node(state: GraphState) -> GraphState:
    """Create the bounded instruction package for the coding-agent process."""

    settings = load_settings()
    state["agent_instruction"] = build_agent_instruction(
        request=state["request"],
        brief=state["brief"],
        formulated_task=state.get("formulated_task", ""),
        task_feedback=state["task_feedback"],
        needs_approval=state["needs_approval"],
        approval_reason=state["approval_reason"],
        approved=state["approved"],
        settings=settings,
        research_sources=list(state.get("research_source_titles", []) or []),
    )

    _log_node_start("5/7", "CREATE_AGENT_INSTRUCTION", "Create coding-agent instruction")
    logger.info("Instruction purpose: merge task, brief, orchestrator identity, project rules, selected skills, allowed directories, stop conditions, and validation expectations.")
    logger.info("Instruction size: %s characters", len(state["agent_instruction"]))
    logger.info("Instruction preview: %s", _single_line_preview(state["agent_instruction"], limit=360))

    return state


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


def _format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


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
    progress_callback: Callable[[str], None] | None = None,
) -> GraphState:
    """Run the configured coding-agent command as a separate process.

    The configured coding agent may modify files on disk. Restart this Python process before relying
    on any Python code changed during the subprocess run.
    """

    _log_node_start("6/7", "RUN_CODING_AGENT", "Run configured coding agent")
    logger.info("[LEARN] This is the RUN_CODING_AGENT node — the final step of the coding workflow graph.")
    logger.info("[LEARN] It launches the configured coding-agent backend as a separate subprocess.")

    settings = load_settings()
    if execute_coding_agent_override is not None:
        settings = replace(settings, execute_coding_agent=execute_coding_agent_override)

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
    logger.info("Working directory: %s", str(PROJECT_ROOT))

    if settings.execute_coding_agent:
        logger.info("[LEARN] Starting coding agent subprocess now. This may take several minutes.")

    result = run_coding_agent(
        agent_instruction=state["agent_instruction"],
        project_root=PROJECT_ROOT,
        settings=settings,
        progress_callback=progress_callback,
    )
    state["coding_agent_result"] = result.summary()
    command = getattr(result, "command", ())
    state["coding_agent_success"] = getattr(result, "success", False)
    state["coding_agent_changed_files"] = getattr(result, "changed_files_delta", ())
    state["coding_agent_command"] = command[0] if command else ""
    state["coding_agent_returncode"] = getattr(result, "returncode", None)
    state["coding_agent_timed_out"] = getattr(result, "timed_out", False)
    state["coding_agent_performed_by"] = settings.coding_agent_command if command else ""

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

    return state


def end_node(state: GraphState) -> GraphState:
    """Log the final request and approval status for review."""

    _log_node_start("7/7", "END_NODE", "Workflow complete")
    logger.info("Task: %s", _request_title(state["request"]))
    logger.info("Approval: required=%s approved=%s", state["needs_approval"], state["approved"])
    logger.info("Approved by: %s", state.get("approved_by", "") or "not required")
    logger.info("Performed by: %s", state.get("coding_agent_performed_by", "") or "not run")
    coding_agent_result = state.get("coding_agent_result", "").strip()
    if coding_agent_result:
        logger.info("Coding agent: %s", coding_agent_result.splitlines()[0])
    else:
        logger.info("Coding agent: not run")
    logger.info("---END---")
    return state


def build_graph(
    checkpointer_storage=None,
    execute_coding_agent_override: bool | None = None,
    coding_agent_progress_callback: Callable[[str], None] | None = None,
):
    """Build and compile the backlog-to-agent-instruction workflow."""

    workflow = StateGraph(GraphState)

    workflow.add_node(NodeName.READ_REQUEST, read_request_node)
    workflow.add_node(NodeName.CHECK_RESEARCH, check_research_node)
    workflow.add_node(NodeName.RESEARCH_GATE, research_gate_node)
    workflow.add_node(NodeName.REVIEW_RISK, review_risk_node)
    workflow.add_node(NodeName.CREATE_BRIEF, create_brief_node)
    workflow.add_node(NodeName.CHECK_CLARIFICATION, check_clarification_node)
    workflow.add_node(NodeName.CLARIFICATION_GATE, clarification_gate_node)
    workflow.add_node(NodeName.FORMULATE_TASK, formulate_task_node)
    workflow.add_node(NodeName.APPROVAL_REQUIRED, approval_required_node)
    workflow.add_node(NodeName.REQUEST_PLAN, request_plan_node)
    workflow.add_node(NodeName.REVIEW_PLAN, review_plan_node)
    workflow.add_node(NodeName.PLAN_HUMAN_GATE, plan_human_gate_node)
    workflow.add_node(
        NodeName.CREATE_AGENT_INSTRUCTION,
        create_agent_instruction_node,
    )
    workflow.add_node(
        NodeName.RUN_CODING_AGENT,
        lambda state: run_coding_agent_node(
            state,
            execute_coding_agent_override=execute_coding_agent_override,
            progress_callback=coding_agent_progress_callback,
        ),
    )
    workflow.add_node(NodeName.END_NODE, end_node)

    workflow.add_edge(START, NodeName.READ_REQUEST)
    workflow.add_edge(NodeName.READ_REQUEST, NodeName.CHECK_RESEARCH)
    workflow.add_conditional_edges(
        NodeName.CHECK_RESEARCH,
        route_after_check_research,
        {
            NodeName.RESEARCH_GATE: NodeName.RESEARCH_GATE,
            NodeName.REVIEW_RISK: NodeName.REVIEW_RISK,
        },
    )
    workflow.add_edge(NodeName.RESEARCH_GATE, NodeName.REVIEW_RISK)
    workflow.add_edge(NodeName.REVIEW_RISK, NodeName.CREATE_BRIEF)
    workflow.add_edge(NodeName.CREATE_BRIEF, NodeName.CHECK_CLARIFICATION)
    workflow.add_conditional_edges(
        NodeName.CHECK_CLARIFICATION,
        route_after_check_clarification,
        {
            NodeName.CLARIFICATION_GATE: NodeName.CLARIFICATION_GATE,
            NodeName.FORMULATE_TASK: NodeName.FORMULATE_TASK,
        },
    )
    workflow.add_edge(NodeName.CLARIFICATION_GATE, NodeName.CHECK_CLARIFICATION)
    workflow.add_conditional_edges(
        NodeName.FORMULATE_TASK,
        route_after_formulate_task,
        {
            NodeName.APPROVAL_REQUIRED: NodeName.APPROVAL_REQUIRED,
            NodeName.REQUEST_PLAN: NodeName.REQUEST_PLAN,
        },
    )
    workflow.add_conditional_edges(
        NodeName.APPROVAL_REQUIRED,
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
            NodeName.PLAN_HUMAN_GATE: NodeName.PLAN_HUMAN_GATE,
        },
    )
    workflow.add_edge(NodeName.PLAN_HUMAN_GATE, NodeName.REQUEST_PLAN)

    workflow.add_edge(NodeName.CREATE_AGENT_INSTRUCTION, NodeName.RUN_CODING_AGENT)
    workflow.add_edge(NodeName.RUN_CODING_AGENT, NodeName.END_NODE)
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(
        interrupt_before=[
            NodeName.RESEARCH_GATE,
            NodeName.APPROVAL_REQUIRED,
            NodeName.CLARIFICATION_GATE,
            NodeName.PLAN_HUMAN_GATE,
        ],
        checkpointer=checkpointer_storage,
    )


graph = build_graph()
