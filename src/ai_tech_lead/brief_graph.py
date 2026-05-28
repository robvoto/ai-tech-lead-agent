from typing import TypedDict
from langgraph.graph import StateGraph, START, END

 

class State(TypedDict):
    """Represents the state of the workflow execution."""
    request: str
    brief: str
    needs_approval: bool

def read_request (state: State) -> State:
    """This node receives the user’s original request and prepares it for processing. It ensures we have a clear starting point."""
    print("---Node-read_request: Reading request---")

    print("User Request" + state['request'])

    return state

def create_brief (state: State) -> State:
    """This node generates a structured execution brief based on the request.
       It outlines objectives, constraints, and acceptance criteria for a safe coding task."""
    
    print("---Node-create_brief: Creating execution brief---")
    
    risky_words = ["delete", "remove", "refactor", "rewrite", "all files", "entire project"]

    state["needs_approval"] = any(
      word in state["request"].lower()
        for word in risky_words
    )

    state["brief"] = """
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

def node_end(state: State) -> State:
    """ This node concludes the workflow, printing the final brief and any approval needs. 
        It ensures we can review before action."""

    print("---Node-node_end: Finishing workflow---") 

    print(f"Request: {state['request']}")     
    print(f"Brief: {state['brief']}") 
    print(f"--------------------") 
    print(f"Needs approval: {state['needs_approval']}") 
    print(f"--------------------") 
    print("---END---\n")

    return state


def assess_risk(state: State) -> State:
    """Check whether the request needs human approval before execution."""

    print("---Node-assess_risk: Checking approval risk---")

    risky_words = ["delete", "remove", "refactor", "rewrite", "all files", "entire project"]

    state["needs_approval"] = any(
        word in state["request"].lower()
        for word in risky_words
    )

    return state


def decision_node(state: State) -> str:
    """Decision node that determines the next node to execute."""
    print("---Node-decision_node: Evaluating decision---")

    if state['needs_approval']:
        return "create_brief"
    else:
        return "node_end"
    

def run_sample_graph() -> None:
    """Execute the workflow nodes for the AI Technical Lead."""
    # This will serve as the entry point for the LangGraph orchestration.
    print("Executing workflow nodes...")
    
    workflow = StateGraph(State)
    workflow.add_node("read_request", read_request)
    workflow.add_node("create_brief", create_brief)
    workflow.add_node("assess_risk", assess_risk)
    workflow.add_node("node_end", node_end)
 
    workflow.add_edge(START, "read_request")
    workflow.add_edge("read_request", "assess_risk")
    workflow.add_conditional_edges("assess_risk", decision_node)
    workflow.add_edge("create_brief", "node_end")
    workflow.add_edge("node_end", END)

    app = workflow.compile()
    
    app.invoke({
        "request": "Build Telegram input",
        "brief": "",
        "needs_approval": False,
    })

    app.invoke({
        "request": "Refactor the entire project",
        "brief": "",
        "needs_approval": False,
    })