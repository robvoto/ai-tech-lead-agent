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
from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "read_request"
    CHECK_APPROVAL = "check_approval"
    CREATE_BRIEF = "create_brief"
    APPROVAL_REQUIRED = "approval_required"
    CREATE_AGENT_INSTRUCTION = "create_agent_instruction"
    RUN_CODING_AGENT = "run_coding_agent"
    END_NODE = "end_node"


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

    logger.info("---------Reading request---------")

    request = state["request"].strip()
    if not request:
        raise ValueError("Request cannot be empty.")

    state["request"] = request
    logger.info("Task: %s", _request_title(state["request"]))
    return state


def check_approval_node(state: GraphState) -> GraphState:
    """Log the explicit approval requirement supplied by the backlog item.

    This is not a risk classifier. It deliberately avoids keyword heuristics.
    TODO: replace or augment this with an LLM risk-review node that returns a
    structured decision with reason, confidence, and required human action.
    """

    logger.info("---------Checking approval---------")
    logger.info(
        "Approval: %s (%s)",
        "required" if state["needs_approval"] else "not required",
        state["approval_reason"],
    )
    return state


def create_brief_node(state: GraphState) -> GraphState:
    """Create a structured execution brief for the selected backlog task."""

    logger.info("---------Creating execution brief---------")

    settings = load_settings()
    state["brief"] = settings.prompts["execution_brief_template"].format(
        request=state["request"],
        relevant_files=_format_bullets(settings.watched_directories),
        constraint_list=_format_bullets(settings.brief_constraints),
        acceptance_criteria=_format_bullets(settings.acceptance_criteria),
        approval_reason=state["approval_reason"],
        risk_notes=_format_bullets(settings.risk_notes),
    )

    return state


def route_after_brief(state: GraphState) -> str:
    """Choose the next LangGraph node name after the brief is created."""

    logger.info("---------Routing after brief---------")

    if state["needs_approval"]:
        logger.info("Route: approval_required")
        return NodeName.APPROVAL_REQUIRED

    logger.info("Route: create_agent_instruction")
    return NodeName.CREATE_AGENT_INSTRUCTION


def route_after_approval(state: GraphState) -> str:
    """Choose the next LangGraph node name after human approval is received."""

    logger.info("---------Routing after approval---------")

    if state["approved"]:
        logger.info("Route: create_agent_instruction")
        return NodeName.CREATE_AGENT_INSTRUCTION

    logger.info("Route: end_node")
    return NodeName.END_NODE


def approval_required_node(state: GraphState) -> GraphState:
    """Human approval gate before creating a coding-agent instruction."""

    logger.info("---------Request requires approval---------")
    logger.info("Approval reason: %s", state["approval_reason"])
    logger.info("This request should not continue without human approval.")

    return state


def create_agent_instruction_node(state: GraphState) -> GraphState:
    """Create the bounded instruction package for the coding-agent process."""

    settings = load_settings()
    agent_instruction = settings.prompts["agent_instruction_template"].format(
        request=state["request"],
        brief=state["brief"],
        needs_approval=state["needs_approval"],
        approval_reason=state["approval_reason"],
        approved=state["approved"],
        max_runtime_minutes=settings.max_runtime_minutes,
        allowed_directories=_format_bullets(settings.allowed_directories),
    )

    state["agent_instruction"] = agent_instruction

    logger.info("---------Creating coding-agent instruction---------")
    logger.info("Instruction created: %s characters", len(state["agent_instruction"]))

    return state


def _format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


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
    """Run the configured Codex CLI command as a separate process.

    Codex may modify files on disk. Restart this Python process before relying
    on any Python code that Codex changed during the subprocess run.
    """

    logger.info("---------Running coding agent---------")

    settings = load_settings()
    if execute_coding_agent_override is not None:
        settings = replace(
            settings,
            execute_coding_agent=execute_coding_agent_override,
        )

    result = run_coding_agent(
        agent_instruction=state["agent_instruction"],
        project_root=PROJECT_ROOT,
        settings=settings,
    )
    state["coding_agent_result"] = result.summary()
    logger.info("Coding agent: %s", result.message or "completed")
    logger.info("Return code: %s", result.returncode)

    return state


def end_node(state: GraphState) -> GraphState:
    """Log the final request and approval status for review."""

    logger.info("---------Workflow complete---------")
    logger.info("Task: %s", _request_title(state["request"]))
    logger.info("Approval required: %s", state["needs_approval"])
    logger.info("Approved: %s", state["approved"])
    logger.info("Coding agent summary: %s", state["coding_agent_result"].splitlines()[0])
    logger.info("---END---")
    return state


def build_graph(
    checkpointer_storage=None,
    execute_coding_agent_override: bool | None = None,
):
    """Build and compile the backlog-to-agent-instruction workflow."""

    workflow = StateGraph(GraphState)

    workflow.add_node(NodeName.READ_REQUEST, read_request_node)
    workflow.add_node(NodeName.CHECK_APPROVAL, check_approval_node)
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
    workflow.add_edge(NodeName.READ_REQUEST, NodeName.CHECK_APPROVAL)
    workflow.add_edge(NodeName.CHECK_APPROVAL, NodeName.CREATE_BRIEF)
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
