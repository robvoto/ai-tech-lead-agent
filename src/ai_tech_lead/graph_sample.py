from typing import TypedDict
from langgraph.graph import StateGraph, START, END


INITIAL_NODE_ID = "initial_node"

class State(TypedDict):
    """Represents the state of the workflow execution."""
    current_node: str

def node_init(state: State) -> State:
    """Simulates the execution of the initial node."""
    print("Executing initial node")
    # Simulate some processing and update the state
    state['current_node'] = INITIAL_NODE_ID
    return state

def node_b(state: State) -> State:
    """Simulates the execution of Node B."""
    print("Executing Node B")
    # Simulate some processing and update the state
    state['current_node'] = 'Node B'
    return state

def node_end(state: State) -> State:
    """Simulates the execution of the final node."""
    print("Executing final node")
    # Simulate some processing and update the state
    state['current_node'] = 'final_node'
    return state

def decision_node(state: State) -> str:
    """Simulates a decision node that determines the next node to execute."""
    print("Evaluating decision node")
    # Simulate a decision based on the current state
    if state['current_node'] == INITIAL_NODE_ID:
        return 'Node B'
    else:
        return 'Final Node'

def run_sample_graph() -> None:
    """Execute the workflow nodes for the AI Technical Lead."""
    # This will serve as the entry point for the LangGraph orchestration.
    print("Executing workflow nodes...")
    
    workflow = StateGraph(State)
    workflow.add_node("node_init", node_init)
    workflow.add_node("node_b", node_b)
    workflow.add_node("node_end", node_end)

    workflow.add_edge(START, "node_init")
    workflow.add_conditional_edges("node_init", decision_node, {"Node B": "node_b", "Final Node": "node_end"})
    workflow.add_edge("node_b", "node_end")
    workflow.add_edge("node_end", END)

    app = workflow.compile()
    app.invoke({"current_node": ""})