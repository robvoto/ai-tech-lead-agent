"""Small LangGraph workflow for producing a safe execution brief.

This module stays focused on the graph itself so LangGraph Studio can import it
without the local demo runner getting in the way.
"""

import logging
from enum import StrEnum
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .app_settings import load_settings
from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class NodeName(StrEnum):
    """Stable LangGraph node names."""

    READ_REQUEST = "read_request"
    ASSESS_RISK = "assess_risk"
    CREATE_BRIEF = "create_brief"
    APPROVAL_REQUIRED = "approval_required"
    EXECUTE_TASK = "execute_task"
    END_NODE = "end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow."""

    request: str
    brief: str
    needs_approval: bool
    approved: bool
    agent_instruction: str

def read_request_node(state: GraphState) -> GraphState:
    """Validate and normalize the incoming user request."""

    logger.info("---------Reading request---------")

    request = state["request"].strip()
    if not request:
        raise ValueError("Request cannot be empty.")

    state["request"] = request
    logger.info("User request: %s", state["request"])
    return state


def assess_risk_node(state: GraphState) -> GraphState:
    """Set the approval flag based on configured deterministic risk terms."""

    logger.info("---------Assessing risk---------")

    settings = load_settings()
    request_lower = state["request"].lower()
    state["needs_approval"] = any(
        risk_term.lower() in request_lower
        for risk_term in settings.risk_terms
    )

    logger.info("Needs approval: %s", state["needs_approval"])
    return state


def create_brief_node(state: GraphState) -> GraphState:
    """Create a structured execution brief for a safe coding task."""

    logger.info("---------Creating execution brief---------")

    settings = load_settings()
    state["brief"] = settings.prompts["execution_brief_template"].format(
        request=state["request"],
        relevant_files=_format_bullets(settings.watched_directories),
        constraint_list=_format_bullets(settings.brief_constraints),
        acceptance_criteria=_format_bullets(settings.acceptance_criteria),
        risk_notes=_format_bullets(settings.risk_notes),
    )

    return state
 

def route_after_brief(state: GraphState) -> str:
    """Choose the next LangGraph node name after the brief is created."""

    logger.info("---------Routing after brief---------")

    if state["needs_approval"]:
        logger.info("Route: approval_required")
        return NodeName.APPROVAL_REQUIRED

    logger.info("Route: execute_task")
    return NodeName.EXECUTE_TASK
 

def route_after_approval(state: GraphState) -> str:
    """Choose the next LangGraph node name after the approval is received."""

    logger.info("---------Routing after approval---------")


    if state["approved"]:
        logger.info("Route: execute_task")
        return NodeName.EXECUTE_TASK

    logger.info("Route: end_node")
    return NodeName.END_NODE


def approval_required_node(state: GraphState) -> GraphState:
    """Mark the point where human approval will be added next."""

    logger.info("---------Request requires approval---------")
    logger.info("This request should not be sent to a coding agent yet.")

    return state


def execute_task_node(state: GraphState) -> GraphState:
    """Prepare the instruction package that would be sent to a coding agent."""

    settings = load_settings()
    agent_instruction = settings.prompts["agent_instruction_template"].format(
        request=state["request"],
        brief=state["brief"],
        needs_approval=state["needs_approval"],
        approved=state["approved"],
        max_runtime_minutes=settings.max_runtime_minutes,
        allowed_directories=_format_bullets(settings.allowed_directories),
    )

    state["agent_instruction"] = agent_instruction

    logger.info("--------- Agent instruction package ---------")
    logger.info("%s", state["agent_instruction"])

    return state


def _format_bullets(values: list[str]) -> str:
    """Format configured list values for prompt templates."""

    return "\n".join(f"- {value}" for value in values)


def end_node(state: GraphState) -> GraphState:
    """Print the final brief and approval status for review."""

    logger.info("---------Finishing workflow---------")
    logger.info("Request: %s", state["request"])
    logger.info("--------------------")
    logger.info("Needs approval: %s", state["needs_approval"])
    logger.info("Approved: %s", state["approved"])
    logger.info("-" * 30)
    logger.info("---END---")
    logger.info("-" * 30)
    return state


def build_graph(checkpointer_storage=None):
    """Build and compile the brief workflow."""

    workflow = StateGraph(GraphState)

    workflow.add_node(NodeName.READ_REQUEST, read_request_node)
    workflow.add_node(NodeName.ASSESS_RISK, assess_risk_node)
    workflow.add_node(NodeName.CREATE_BRIEF, create_brief_node)
    workflow.add_node(NodeName.APPROVAL_REQUIRED, approval_required_node)
    workflow.add_node(NodeName.EXECUTE_TASK, execute_task_node)
    workflow.add_node(NodeName.END_NODE, end_node)

    workflow.add_edge(START, NodeName.READ_REQUEST)
    workflow.add_edge(NodeName.READ_REQUEST, NodeName.ASSESS_RISK)
    workflow.add_edge(NodeName.ASSESS_RISK, NodeName.CREATE_BRIEF)
    workflow.add_conditional_edges(
        NodeName.CREATE_BRIEF,
        route_after_brief,
        {
            NodeName.APPROVAL_REQUIRED: NodeName.APPROVAL_REQUIRED,
            NodeName.EXECUTE_TASK: NodeName.EXECUTE_TASK,
        },
    ) 
    workflow.add_conditional_edges(
        NodeName.APPROVAL_REQUIRED,
        route_after_approval,
        {
            NodeName.EXECUTE_TASK: NodeName.EXECUTE_TASK,
            NodeName.END_NODE: NodeName.END_NODE,            
        },
    )

    workflow.add_edge(NodeName.EXECUTE_TASK, NodeName.END_NODE)
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(
        interrupt_before=[NodeName.APPROVAL_REQUIRED],
        checkpointer=checkpointer_storage,
    )


graph = build_graph()
