"""Utilities for exporting LangGraph diagrams to the local docs directory."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langchain_core.runnables.graph import CurveStyle, Edge, Graph, Node
from langgraph.checkpoint.memory import MemorySaver

from .app_settings import AppSettings
from .coding_workflow_graph import NodeName, build_graph
from .config import GRAPH_DIAGRAM_PATH, ensure_project_dirs
from .logging_setup import LOGGER_NAME, configure_logging

logger = logging.getLogger(LOGGER_NAME)
LOG_SEPARATOR = "----------------------------------------"
PRESENTATION_CURVE_STYLE = CurveStyle.STEP_AFTER
PRESENTATION_PADDING = 24
RUN_BUDGET_SCOPE_NODE_ID = "__run_budget_guard_scope__"
RUN_BUDGET_SCOPE_LABEL = "Budget guard on orchestrator LLM steps"
RUN_BUDGET_INTERRUPT_LABEL = "Run budget interrupt"


@dataclass(frozen=True)
class GraphDiagramSpec:
    """One graph export target and the builder used to render it."""

    name: str
    path: Path
    requires_settings: bool
    build_app: Callable[[AppSettings | None], Any]


def export_graph_diagrams(*, settings: AppSettings | None = None) -> list[Path]:
    """Export implemented graph diagrams into `docs/`."""

    ensure_project_dirs()
    logger.info(LOG_SEPARATOR)
    logger.info(
        "Graph diagram export: refreshing implemented graphs in %s.",
        GRAPH_DIAGRAM_PATH.parent,
    )

    updated_paths: list[Path] = []

    for spec in _graph_diagram_specs():
        app = spec.build_app(settings)
        runtime_graph = app.get_graph()
        graph = _presentation_graph(runtime_graph)
        mermaid_source = graph.draw_mermaid(curve_style=PRESENTATION_CURVE_STYLE)
        current_hash = hashlib.sha256(mermaid_source.encode()).hexdigest()
        spec.path.parent.mkdir(parents=True, exist_ok=True)
        hash_path = spec.path.with_suffix(".hash")
        if hash_path.exists() and hash_path.read_text().strip() == current_hash:
            logger.info("Graph diagram unchanged, skipping: %s (%s)", spec.path, spec.name)
            continue
        png_bytes = graph.draw_mermaid_png(
            curve_style=PRESENTATION_CURVE_STYLE,
            padding=PRESENTATION_PADDING,
        )
        spec.path.write_bytes(png_bytes)
        hash_path.write_text(current_hash)
        updated_paths.append(spec.path)
        logger.info("Graph diagram refreshed: %s (%s)", spec.path, spec.name)

    if updated_paths:
        logger.info(
            "Graph diagram export complete: %s", ", ".join(str(path) for path in updated_paths)
        )
    else:
        logger.info("Graph diagram export complete: no diagrams were exported.")
    return updated_paths


def _presentation_graph(runtime_graph: Graph) -> Graph:
    """Return a readable diagram view without changing the executable graph.

    Run-budget handling is a cross-cutting concern: many budgeted nodes can enter the
    same interrupt and the interrupt can resume the exact blocked node. Rendering all
    of those physical edges dominates the diagram, so the docs view collapses them to
    one explicit budget-scope annotation and two representative edges.
    """

    budget_node_id = NodeName.RUN_BUDGET_INTERRUPT.value
    if budget_node_id not in runtime_graph.nodes:
        return runtime_graph

    nodes = dict(runtime_graph.nodes)
    budget_node = nodes[budget_node_id]
    nodes[budget_node_id] = Node(
        id=budget_node.id,
        name=RUN_BUDGET_INTERRUPT_LABEL,
        data=budget_node.data,
        metadata=budget_node.metadata,
    )
    nodes[RUN_BUDGET_SCOPE_NODE_ID] = Node(
        id=RUN_BUDGET_SCOPE_NODE_ID,
        name=RUN_BUDGET_SCOPE_LABEL,
        data=None,
        metadata=None,
    )

    edges = [
        edge
        for edge in runtime_graph.edges
        if edge.source != budget_node_id and edge.target != budget_node_id
    ]
    edges.extend(
        [
            Edge(
                source=RUN_BUDGET_SCOPE_NODE_ID,
                target=budget_node_id,
                data="budget exceeded",
                conditional=True,
            ),
            Edge(
                source=budget_node_id,
                target=RUN_BUDGET_SCOPE_NODE_ID,
                data="retry blocked step / stop",
                conditional=True,
            ),
        ]
    )
    return Graph(nodes=nodes, edges=edges)


def _graph_diagram_specs() -> tuple[GraphDiagramSpec, ...]:
    return (
        GraphDiagramSpec(
            name="coding_workflow",
            path=GRAPH_DIAGRAM_PATH,
            requires_settings=False,
            build_app=lambda _settings: build_graph(
                checkpointer_storage=MemorySaver(),
                execute_coding_agent_override=False,
            ),
        ),
    )


def main() -> None:
    """Refresh graph diagrams as a standalone debug helper."""

    configure_logging(debug=False)
    export_graph_diagrams()


if __name__ == "__main__":
    main()
