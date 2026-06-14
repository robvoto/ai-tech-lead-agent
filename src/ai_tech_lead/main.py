"""Command-line entry point for the local AI Technical Lead Assistant."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread
from typing import Callable, Sequence

from ai_tech_lead.admin_server import run_admin_server
from ai_tech_lead.app_settings import AppSettings, load_settings
from ai_tech_lead.config import PROJECT_ROOT, SETTINGS_PATH
from ai_tech_lead.graph_diagrams import export_graph_diagrams
from ai_tech_lead.logging_setup import LOGGER_NAME, configure_logging
from ai_tech_lead.storage import initialize_database
from ai_tech_lead.telegram_operator import run_telegram_operator

logger = logging.getLogger(LOGGER_NAME)
_RELOAD_WATCHED_DIRECTORIES: tuple[str, ...] = ("src", "config", "docs")
_RELOAD_POLL_INTERVAL_SECONDS = 0.5
_RELOAD_TERMINATE_TIMEOUT_SECONDS = 5.0


def main() -> int:
    """Initialize local storage and start the configured local services."""

    args = _parse_args()
    configure_logging(debug=args.debug)

    if args.reload:
        return _run_with_reload(_reload_command_args())

    return _run_services(debug=args.debug)


def _run_services(*, debug: bool) -> int:
    db_path = initialize_database()

    logger.info("AI Technical Lead Assistant started")
    logger.info("Orchestrator codebase: %s", PROJECT_ROOT)
    logger.info("Default project root (hardcoded for now): %s", PROJECT_ROOT)
    logger.info("SQLite ready: %s", db_path)

    settings = _load_settings_for_startup()
    if settings is not None:
        logger.info(
            "Target project root from settings: %s (current default is hardcoded above)",
            settings.project_root,
        )
    if debug and settings is not None:
        export_graph_diagrams(settings=settings)
    admin_host, admin_port = _admin_bind_address(settings)
    service_threads = [
        _start_service_thread(
            "admin-server",
            lambda: run_admin_server(admin_host, admin_port, SETTINGS_PATH),
        )
    ]

    if settings is not None and settings.telegram_enabled:
        service_threads.append(_start_service_thread("telegram-operator", run_telegram_operator))
    elif settings is not None:
        logger.info("Telegram operator disabled by settings.")

    try:
        for thread in service_threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")

    return 0


def _parse_args() -> argparse.Namespace:
    """Parse debug-only command-line options."""

    parser = argparse.ArgumentParser(description="Run the AI Tech Lead prototype.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for local diagnostics.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the process when source, config, or prompt files change.",
    )
    return parser.parse_args()


def _start_service_thread(name: str, target: Callable[[], None]) -> Thread:
    thread = Thread(target=target, name=name)
    thread.start()
    return thread


def _load_settings_for_startup() -> AppSettings | None:
    try:
        return load_settings()
    except (FileNotFoundError, ValueError) as error:
        logger.error("Unable to load settings for startup: %s", error)
        return None


def _reload_command_args() -> list[str]:
    return [arg for arg in sys.argv[1:] if arg != "--reload"]


def _run_with_reload(command_args: Sequence[str]) -> int:
    """Restart the whole process when watched files change."""

    watched_paths = _reload_watched_paths()
    previous_snapshot = _reload_snapshot(watched_paths)
    command = [sys.executable, "-m", "ai_tech_lead", *command_args]

    logger.info(
        "Hot reload enabled; watching %s.",
        ", ".join(str(path) for path in watched_paths),
    )

    while True:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT)
        try:
            while True:
                exit_code = process.poll()
                if exit_code is not None:
                    return exit_code

                current_snapshot = _reload_snapshot(watched_paths)
                if current_snapshot != previous_snapshot:
                    logger.info("Source change detected; restarting the process.")
                    _terminate_process(process)
                    previous_snapshot = current_snapshot
                    break

                time.sleep(_RELOAD_POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Hot reload stopped.")
            _terminate_process(process)
            return 0


def _reload_watched_paths() -> tuple[Path, ...]:
    return tuple(PROJECT_ROOT / relative_path for relative_path in _RELOAD_WATCHED_DIRECTORIES)


def _reload_snapshot(paths: Sequence[Path]) -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for base_path in paths:
        if not base_path.exists():
            continue
        for file_path in base_path.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                snapshot[str(file_path.relative_to(PROJECT_ROOT))] = file_path.stat().st_mtime_ns
            except OSError:
                continue
    return snapshot


def _terminate_process(process: subprocess.Popen[object]) -> None:
    process.terminate()
    try:
        process.wait(timeout=_RELOAD_TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=_RELOAD_TERMINATE_TIMEOUT_SECONDS)


def _admin_bind_address(settings: AppSettings | None) -> tuple[str, int]:
    """Return the admin server bind address from settings, or bootstrap defaults.

    The bootstrap fallback keeps the admin screen reachable on first start before
    local settings exist. Once settings are available, the bind address comes
    from validated admin configuration.
    """

    if settings is not None:
        return settings.admin_bind_host, settings.admin_bind_port

    return "127.0.0.1", 8766


if __name__ == "__main__":
    raise SystemExit(main())
