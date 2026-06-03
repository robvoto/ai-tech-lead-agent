"""Terminal demo helpers for the brief graph workflow."""

import os
import logging
 

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from .backlog_loader import backlog_item_to_graph_state, load_backlog_item_by_id
from .brief_graph import GraphState, build_graph
from .logging_setup import LOGGER_NAME, configure_logging

logger = logging.getLogger(LOGGER_NAME)


RISKY_SAMPLE_INPUT: GraphState = {
    "request": "Refactor the entire project",
    "brief": "",
    "needs_approval": False,
    "approved": False,
    "agent_instruction": "",
}


def save_graph_diagram(app) -> None:
    """Save a generated PNG diagram of the compiled LangGraph workflow."""

    png_bytes = app.get_graph().draw_mermaid_png()

    with open("graph_diagram.png", "wb") as file:
        file.write(png_bytes)

    logger.info("Graph diagram saved to graph_diagram.png")


def run_sample_graph() -> None:
    """Run two sample requests from the terminal."""

    configure_logging()

    memory = MemorySaver()
    app = build_graph(checkpointer_storage=memory)
    save_graph_diagram(app)

    backlog_item = load_backlog_item_by_id("JH-001")
    backlog_input = backlog_item_to_graph_state(backlog_item)
    logger.info("Loaded backlog item: %s - %s", backlog_item.item_id, backlog_item.title)

    thread_backlog: RunnableConfig = {
        "configurable": {"thread_id": f"backlog-{backlog_item.item_id}"}
    }

    app.invoke(
        backlog_input,
        config=thread_backlog,
    )

    thread_risky: RunnableConfig = {
        "configurable": {"thread_id": "sample-risky-request"}
    }

    app.invoke(
        RISKY_SAMPLE_INPUT,
        config=thread_risky,
    )

    state = app.get_state(thread_risky)
    logger.info("Final graph state:")
    logger.info("Request: %s", state.values["request"])
    logger.info("Brief: %s", state.values["brief"])
    logger.info("Needs approval: %s", state.values["needs_approval"])
    logger.info("Approved: %s", state.values["approved"])
    logger.info("Next state: %s", state.next)

    user_approval = input("\nApprove the risky request? (y/n): ").strip().lower()

    if user_approval == "y":
        app.update_state(
            thread_risky,
            {"approved": True},
        )
        logger.info("User approved the risky request. Resuming graph...")
        app.invoke(
            None,
            config=thread_risky,
        )
    else:
        app.update_state(
            thread_risky,
            {"approved": False},
        )
        logger.info("User rejected the risky request. Resuming graph to close cleanly...")
        app.invoke(
            None,
            config=thread_risky,
        )
