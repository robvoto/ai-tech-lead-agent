"""Small LangGraph workflow for producing a safe coding-agent instruction.

This module stays focused on the graph itself so LangGraph Studio can import it
without the local CLI runner getting in the way.
"""

import logging
from dataclasses import replace
from enum import StrEnum
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .app_settings import load_settings
from .coding_agent_runner import run_coding_agent
from .config import PROJECT_ROOT
from .instruction_assembler import build_agent_instruction
from .logging_setup import LOGGER_NAME
from .risk_reviewer import review_task_risk

logger = logging.getLogger(LOGGER_NAME)

LOG_SEPARATOR = "----------------------------------------"


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "1_read_request"
    REVIEW_RISK = "2_review_risk"
    CREATE_BRIEF = "3_create_brief"
    APPROVAL_REQUIRED = "4_approval_required"
    CREATE_AGENT_INSTRUCTION = "5_create_agent_instruction"
    RUN_CODING_AGENT = "6_run_coding_agent"
    END_NODE = "7_end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow."""

    request: str
    brief: str
    needs_approval: bool
    approval_reason: str
    approved: bool
    agent_instruction: str
    coding_agent_result: str


def read_request_node(state: GraphState) -> GraphState:
    """Validate and normalize the incoming backlog request."""

    _log_node_start("1/6", "READ_REQUEST", "Read backlog request")

    request = state["request"].strip()
    if not request:
        raise ValueError("Request cannot be empty.")

    state["request"] = request
    logger.info("Task: %s", _request_title(state["request"]))
    return state


def review_risk_node(state: GraphState) -> GraphState:
    """Decide whether this task needs human approval.

    The graph owns this decision now. The backlog may describe the task, but it
    no longer decides whether approval is required.
    """

    _log_node_start("2/6", "REVIEW_RISK", "Review task risk")
    settings = load_settings()
    logger.info("AI channel: OpenAI Responses API")
    logger.info("AI model: %s", settings.orchestrator_ai_model)
    logger.info("AI enabled: %s", settings.orchestrator_ai_enabled)
    decision = review_task_risk(state["request"])
    state["needs_approval"] = decision.needs_approval
    state["approval_reason"] = decision.approval_reason
    logger.info(
        "Approval: %s (%s)",
        "required" if state["needs_approval"] else "not required",
        state["approval_reason"],
    )
    return state


def create_brief_node(state: GraphState) -> GraphState:
    """Create a structured execution brief for the selected backlog task."""

    _log_node_start("3/6", "CREATE_BRIEF", "Create execution brief")

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


def route_after_brief(state: GraphState) -> str:
    """Choose the next LangGraph node name after the brief is created."""

    _log_decision_start("4/6", "ROUTE_AFTER_BRIEF", "Choose next graph path after brief")

    if state["needs_approval"]:
        logger.info("Decision: needs_approval=True -> next node: approval_required")
        return NodeName.APPROVAL_REQUIRED

    logger.info("Decision: needs_approval=False -> next node: create_agent_instruction")
    return NodeName.CREATE_AGENT_INSTRUCTION


def route_after_approval(state: GraphState) -> str:
    """Choose the next LangGraph node name after human approval is received."""

    _log_decision_start("approval", "ROUTE_AFTER_APPROVAL", "Choose next graph path after human decision")

    if state["approved"]:
        logger.info("Decision: approved=True -> next node: create_agent_instruction")
        return NodeName.CREATE_AGENT_INSTRUCTION

    logger.info("Decision: approved=False -> next node: end_node")
    return NodeName.END_NODE


def approval_required_node(state: GraphState) -> GraphState:
    """Human approval gate before creating a coding-agent instruction."""

    _log_node_start("approval", "APPROVAL_REQUIRED", "Pause for human approval")
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
        needs_approval=state["needs_approval"],
        approval_reason=state["approval_reason"],
        approved=state["approved"],
        settings=settings,
    )

    _log_node_start("5/6", "CREATE_AGENT_INSTRUCTION", "Create coding-agent instruction")
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
) -> GraphState:
    """Run the configured coding-agent command as a separate process.

    The configured coding agent may modify files on disk. Restart this Python process before relying
    on any Python code changed during the subprocess run.
    """

    _log_node_start("6/6", "RUN_CODING_AGENT", "Run configured coding agent")
    logger.info("[LEARN] This is the RUN_CODING_AGENT node — the final step of the coding workflow graph.")
    logger.info("[LEARN] It launches your configured coding agent (e.g. Codex) as a separate subprocess.")

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
    )
    state["coding_agent_result"] = result.summary()

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
        "[LEARN] Token usage: not available from the current Codex CLI runner. "
        "The subprocess runs as a separate process and does not expose its LLM token counts."
    )
    logger.info("[LEARN] Cost: not available for the same reason.")

    return state


def end_node(state: GraphState) -> GraphState:
    """Log the final request and approval status for review."""

    _log_node_start("end", "END_NODE", "Workflow complete")
    logger.info("Task: %s", _request_title(state["request"]))
    logger.info("Approval: required=%s approved=%s", state["needs_approval"], state["approved"])
    coding_agent_result = state["coding_agent_result"].strip()
    if coding_agent_result:
        logger.info("Coding agent: %s", coding_agent_result.splitlines()[0])
    else:
        logger.info("Coding agent: not run")
    logger.info("---END---")
    return state


def build_graph(
    checkpointer_storage=None,
    execute_coding_agent_override: bool | None = None,
):
    """Build and compile the backlog-to-agent-instruction workflow."""

    workflow = StateGraph(GraphState)

    workflow.add_node(NodeName.READ_REQUEST, read_request_node)
    workflow.add_node(NodeName.REVIEW_RISK, review_risk_node)
    workflow.add_node(NodeName.CREATE_BRIEF, create_brief_node)
    workflow.add_node(NodeName.APPROVAL_REQUIRED, approval_required_node)
    workflow.add_node(
        NodeName.CREATE_AGENT_INSTRUCTION,
        create_agent_instruction_node,
    )
    workflow.add_node(
        NodeName.RUN_CODING_AGENT,
        lambda state: run_coding_agent_node(
            state,
            execute_coding_agent_override=execute_coding_agent_override,
        ),
    )
    workflow.add_node(NodeName.END_NODE, end_node)

    workflow.add_edge(START, NodeName.READ_REQUEST)
    workflow.add_edge(NodeName.READ_REQUEST, NodeName.REVIEW_RISK)
    workflow.add_edge(NodeName.REVIEW_RISK, NodeName.CREATE_BRIEF)
    workflow.add_conditional_edges(
        NodeName.CREATE_BRIEF,
        route_after_brief,
        {
            NodeName.APPROVAL_REQUIRED: NodeName.APPROVAL_REQUIRED,
            NodeName.CREATE_AGENT_INSTRUCTION: NodeName.CREATE_AGENT_INSTRUCTION,
        },
    )
    workflow.add_conditional_edges(
        NodeName.APPROVAL_REQUIRED,
        route_after_approval,
        {
            NodeName.CREATE_AGENT_INSTRUCTION: NodeName.CREATE_AGENT_INSTRUCTION,
            NodeName.END_NODE: NodeName.END_NODE,
        },
    )

    workflow.add_edge(NodeName.CREATE_AGENT_INSTRUCTION, NodeName.RUN_CODING_AGENT)
    workflow.add_edge(NodeName.RUN_CODING_AGENT, NodeName.END_NODE)
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(
        interrupt_before=[NodeName.APPROVAL_REQUIRED],
        checkpointer=checkpointer_storage,
    )


graph = build_graph()
