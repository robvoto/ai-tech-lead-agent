"""Utilities for exporting LangGraph diagrams to the local data directory."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.memory import MemorySaver

from .app_settings import AppSettings, load_settings
from .coding_workflow_graph import build_graph
from .config import (
    GRAPH_DIAGRAM_PATH,
    TELEGRAM_AGENT_GRAPH_DIAGRAM_PATH,
    ensure_project_dirs,
)
from .logging_setup import LOGGER_NAME, configure_logging
from .telegram_agent_graph import build_telegram_agent_graph

logger = logging.getLogger(LOGGER_NAME)
LOG_SEPARATOR = "----------------------------------------"


@dataclass(frozen=True)
class GraphDiagramSpec:
    """One graph export target and the builder used to render it."""

    name: str
    path: Path
    requires_settings: bool
    build_app: Callable[[AppSettings | None], Any]


def export_graph_diagrams(*, settings: AppSettings | None = None) -> list[Path]:
    """Export implemented graph diagrams into `data/`."""

    ensure_project_dirs()
    resolved_settings = settings or _load_settings_if_needed()
    logger.info(LOG_SEPARATOR)
    logger.info(
        "Graph diagram export: refreshing implemented graphs in %s.",
        GRAPH_DIAGRAM_PATH.parent,
    )

    updated_paths: list[Path] = []

    for spec in _graph_diagram_specs():
        if spec.requires_settings and resolved_settings is None:
            logger.info("Graph diagram skipped: %s requires settings.", spec.name)
            continue
        app = spec.build_app(resolved_settings)
        png_bytes = app.get_graph().draw_mermaid_png()
        spec.path.write_bytes(png_bytes)
        updated_paths.append(spec.path)
        logger.info("Graph diagram refreshed: %s (%s)", spec.path, spec.name)

    if updated_paths:
        logger.info(
            "Graph diagram export complete: %s", ", ".join(str(path) for path in updated_paths)
        )
    else:
        logger.info("Graph diagram export complete: no diagrams were exported.")
    return updated_paths


def _load_settings_if_needed() -> AppSettings | None:
    try:
        return load_settings()
    except (FileNotFoundError, ValueError) as error:
        logger.info("Graph diagram export skipped settings load: %s", error)
        return None


def _require_settings(settings: AppSettings | None) -> AppSettings:
    if settings is None:
        raise RuntimeError(
            "Graph diagram export requires loaded settings for the Telegram agent graph."
        )
    return settings


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
        GraphDiagramSpec(
            name="telegram_agent",
            path=TELEGRAM_AGENT_GRAPH_DIAGRAM_PATH,
            requires_settings=True,
            build_app=lambda settings: build_telegram_agent_graph(
                settings=_require_settings(settings),
                checkpointer=MemorySaver(),
            ),
        ),
    )


def main() -> None:
    """Refresh graph diagrams as a standalone debug helper."""

    configure_logging(debug=False)
    export_graph_diagrams(settings=load_settings())


if __name__ == "__main__":
    main()
