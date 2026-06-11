"""Run one selected backlog item through the coding workflow graph.

This runner is the normal local prototype path.
It does not contain fake sample requests.

Selection rule:
- The caller must provide an explicit backlog item ID.
- The runner loads exactly that item and sends it into the graph.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from .backlog_loader import backlog_item_to_graph_state, load_backlog_item_by_id
from .coding_workflow_graph import build_graph
from .config import GRAPH_DIAGRAM_PATH, ensure_project_dirs
from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def run_backlog_graph(
    task_id: str,
    execute_coding_agent_override: bool | None = None,
) -> None:
    """Run one explicit backlog task through the local graph."""

    memory = MemorySaver()
    app = build_graph(
        checkpointer_storage=memory,
        execute_coding_agent_override=execute_coding_agent_override,
    )
    save_graph_diagram(app)

    try:
        backlog_item = load_backlog_item_by_id(task_id)
    except ValueError as error:
        logger.error("%s", error)
        return
    backlog_input = backlog_item_to_graph_state(backlog_item)

    logger.info("Selected backlog item: %s - %s", backlog_item.item_id, backlog_item.title)

    thread_config: RunnableConfig = {
        "configurable": {"thread_id": f"backlog-{backlog_item.item_id}"}
    }

    app.invoke(
        backlog_input,
        config=thread_config,
    )

    state = app.get_state(thread_config)
    if not state.next:
        logger.info("Graph completed without approval pause.")
        return

    logger.info("Graph paused before next node: %s", state.next)
    user_approval = input("\nApprove this backlog task? (y/n): ").strip().lower()

    app.update_state(
        thread_config,
        {"approved": user_approval == "y"},
    )

    if user_approval == "y":
        logger.info("User approved backlog task. Resuming graph...")
    else:
        logger.info("User rejected backlog task. Resuming graph to close cleanly...")

    app.invoke(
        None,
        config=thread_config,
    )


def save_graph_diagram(app) -> None:
    """Save a generated PNG diagram of the compiled LangGraph workflow."""

    ensure_project_dirs()
    png_bytes = app.get_graph().draw_mermaid_png()
    GRAPH_DIAGRAM_PATH.write_bytes(png_bytes)
    logger.info("Graph diagram: %s", GRAPH_DIAGRAM_PATH)
