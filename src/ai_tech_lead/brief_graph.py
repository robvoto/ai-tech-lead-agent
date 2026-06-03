"""Small LangGraph workflow for producing a safe execution brief.

This module is intentionally simple. It is the first project-shaped graph used to
learn LangGraph concepts before adding model calls, Telegram input, or coding-agent
handoffs.

Current learning focus:
- graph state
- nodes
- conditional routing
- checkpoint storage
- Studio export

Interrupts are intentionally NOT added yet. The next exercise can add them cleanly.
"""

from enum import StrEnum
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableConfig

class NodeName(StrEnum):
    """Stable LangGraph node names.

    These are the names LangGraph uses internally to connect nodes.
    They are not the same thing as the Python function names.
    """

    READ_REQUEST = "read_request"
    ASSESS_RISK = "assess_risk"
    CREATE_BRIEF = "create_brief"
    APPROVAL_REQUIRED = "approval_required"
    END_NODE = "end_node"


class GraphState(TypedDict):
    """State carried through the brief-generation workflow.

    request:
        The original user request, after basic cleanup.

    brief:
        The execution brief created for a future coding agent.

    needs_approval:
        True when the request looks risky and should require human approval.
    """

    request: str
    brief: str
    needs_approval: bool


RISKY_WORDS = [
    "delete",
    "remove",
    "refactor",
    "rewrite",
    "all files",
    "entire project",
]


SAFE_SAMPLE_INPUT: GraphState = {
    "request": "Build Telegram input",
    "brief": "",
    "needs_approval": False,
}


RISKY_SAMPLE_INPUT: GraphState = {
    "request": "Refactor the entire project",
    "brief": "",
    "needs_approval": False,
}


def read_request_node(state: GraphState) -> GraphState:
    """Validate and normalize the incoming user request."""

    print("---------Reading request---------")

    # Normalize the request so later nodes do not have to care about
    # accidental leading/trailing spaces.
    request = state["request"].strip()

    if not request:
        raise ValueError("Request cannot be empty.")

    state["request"] = request
    print(f"User request: {state['request']}")

    return state


def assess_risk_node(state: GraphState) -> GraphState:
    """Set the approval flag based on simple deterministic risk words."""

    print("---------Assessing risk---------")

    request_lower = state["request"].lower()

    state["needs_approval"] = any(
        risky_word in request_lower
        for risky_word in RISKY_WORDS
    )

    print(f"Needs approval: {state['needs_approval']}")
    return state


def create_brief_node(state: GraphState) -> GraphState:
    """Create a structured execution brief for a safe coding task."""

    print("---------Creating execution brief---------")

    state["brief"] = f"""
Request:
- {state["request"]}

Relevant files:
- Not identified yet

Constraints:
- Keep the change small
- Do not touch unrelated files
- Do not add fallback logic without approval
- Do not claim completion without validation evidence

Acceptance criteria:
- Code runs successfully
- Changed files are listed
- Validation command and result are reported

Risk notes:
- Human approval required before broad refactors or destructive changes
""".strip()

    return state


def route_after_brief(state: GraphState) -> str:
    """Choose the next LangGraph node name after the brief is created.

    This is a routing function, not a graph node.

    It returns a node name string. LangGraph then uses that returned name to
    decide where the workflow goes next.
    """

    print("---------Routing after brief---------")

    if state["needs_approval"]:
        print("Route: approval_required")
        return NodeName.APPROVAL_REQUIRED

    print("Route: end_node")
    return NodeName.END_NODE


def approval_required_node(state: GraphState) -> GraphState:
    """Mark the point where human approval will be added next.

    Today this node only prints a warning.
    The next exercise will make this node actually pause the graph.
    """

    print("---------Request requires approval---------")
    print("This request should not be sent to a coding agent yet.")

    return state


def end_node(state: GraphState) -> GraphState:
    """Print the final brief and approval status for review."""

    print("---------Finishing workflow---------")
    print(f"Request: {state['request']}")
    print(f"Brief: {state['brief']}")
    print("--------------------")
    print(f"Needs approval: {state['needs_approval']}")
    print("--------------------")
    print("---END---\n")

    return state


def build_graph(checkpointer_storage=None):
    """Build and compile the brief workflow.

    checkpointer:
        Optional checkpoint storage.
        It is needed later for pause/resume behavior.
        Passing it here does not create an interrupt by itself.
    """

    workflow = StateGraph(GraphState)

    # Register graph nodes.
    # First argument = LangGraph node name.
    # Second argument = Python function to run for that node.
    workflow.add_node(NodeName.READ_REQUEST, read_request_node)
    workflow.add_node(NodeName.ASSESS_RISK, assess_risk_node)
    workflow.add_node(NodeName.CREATE_BRIEF, create_brief_node)
    workflow.add_node(NodeName.APPROVAL_REQUIRED, approval_required_node)
    workflow.add_node(NodeName.END_NODE, end_node)

    # Fixed path.
    workflow.add_edge(START, NodeName.READ_REQUEST)
    workflow.add_edge(NodeName.READ_REQUEST, NodeName.ASSESS_RISK)
    workflow.add_edge(NodeName.ASSESS_RISK, NodeName.CREATE_BRIEF)

    # Conditional path.
    # route_after_brief returns either "approval_required" or "end_node".
    # path_map makes that routing explicit for LangGraph Studio diagrams.
    workflow.add_conditional_edges(
        NodeName.CREATE_BRIEF,
        route_after_brief,
        {
            NodeName.APPROVAL_REQUIRED: NodeName.APPROVAL_REQUIRED,
            NodeName.END_NODE: NodeName.END_NODE,
        },
    )

    # End paths.
    workflow.add_edge(NodeName.APPROVAL_REQUIRED, END)
    workflow.add_edge(NodeName.END_NODE, END)

    return workflow.compile(
        interrupt_before=[NodeName.APPROVAL_REQUIRED],
        checkpointer=checkpointer_storage)


def run_sample_graph() -> None:
    """Run two sample requests from the terminal.

    This is for local testing only.
    LangGraph Studio does not call this function.
    """

    # Temporary checkpoint storage for this local test run.
    memory = MemorySaver()

    # app = local compiled graph used by this terminal test.
    app = build_graph(checkpointer_storage=memory)
    save_graph_diagram(app)

    thread_safe: RunnableConfig = {
        "configurable": {"thread_id": "sample-safe-request"}
    }

    app.invoke(
        SAFE_SAMPLE_INPUT,
        config=thread_safe,
    )
    
    thread_risky: RunnableConfig = {
        "configurable": {"thread_id": "sample-risky-request"}
    }
    
    app.invoke(
        RISKY_SAMPLE_INPUT,
        config=thread_risky,
    )

    state = app.get_state(thread_risky)
    print("\nFinal graph state:")
    
    state_string = str(state)
    for piece in state_string.split(", "):
        print(piece)


    print("\nNext state:", state.next)

def save_graph_diagram(app) -> None:
    """Save a generated PNG diagram of the compiled LangGraph workflow."""

    png_bytes = app.get_graph().draw_mermaid_png()

    with open("graph_diagram.png", "wb") as file:
        file.write(png_bytes)

    print("Graph diagram saved to graph_diagram.png")


# graph = exported compiled graph used by LangGraph Studio.
# This does not run the graph. It only lets Studio import and run it.
graph = build_graph()
