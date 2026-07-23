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
from ai_tech_lead.agent_manifest import render_agent_manifest
from ai_tech_lead.app_settings import AppSettings, load_settings
from ai_tech_lead.config import PROJECT_ROOT, SETTINGS_PATH
from ai_tech_lead.graph_diagrams import export_graph_diagrams
from ai_tech_lead.knowledge_store import (
    backup_knowledge_store,
    compact_knowledge_store,
    get_knowledge_store_statistics,
    restore_knowledge_store,
)
from ai_tech_lead.logging_setup import LOGGER_NAME, configure_logging
from ai_tech_lead.project_pack_templates import bootstrap_project_pack
from ai_tech_lead.runtime_lock import list_active_project_execution_locks
from ai_tech_lead.storage import initialize_database
from ai_tech_lead.telegram_operator import run_telegram_operator
from ai_tech_lead.workspace_ops import bootstrap_workspace, doctor_workspace

logger = logging.getLogger(LOGGER_NAME)
_RELOAD_WATCHED_DIRECTORIES: tuple[str, ...] = ("src", "config", "docs")
_RELOAD_POLL_INTERVAL_SECONDS = 0.5
_RELOAD_TERMINATE_TIMEOUT_SECONDS = 5.0
_RELOAD_AGENT_LOCK_WAIT_SECONDS = 1800  # max seconds to defer reload while agent runs


def main() -> int:
    """Initialize local storage and start the configured local services."""

    args = _parse_args()

    # JSON subprocess entry point — non-interactive, no Telegram, no admin UI
    if getattr(args, "command", None) == "run-agent-task":
        configure_logging(debug=args.debug)
        from ai_tech_lead.agent_task_runner import run_agent_task
        return run_agent_task(args.input_json, args.output_json)

    if getattr(args, "command", None) == "setup":
        configure_logging(debug=args.debug)
        return _run_setup()

    if getattr(args, "command", None) == "doctor":
        configure_logging(debug=args.debug)
        return _run_doctor()

    if getattr(args, "command", None) == "bootstrap-project-pack":
        configure_logging(debug=args.debug)
        return _run_bootstrap_project_pack(args.target_root, overwrite=args.overwrite)

    if getattr(args, "command", None) == "manifest":
        return _run_manifest()

    if getattr(args, "command", None) == "knowledge-store":
        configure_logging(debug=args.debug)
        return _run_knowledge_store(args)

    if getattr(args, "command", None) == "backlog-sync-recover":
        configure_logging(debug=args.debug)
        return _run_backlog_sync_recover()

    configure_logging(debug=args.debug)

    if args.reload:
        return _run_with_reload(_reload_command_args())

    if getattr(args, "admin", False):
        return _run_admin_screen()

    return _run_services()


def _run_services() -> int:
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


def _run_admin_screen() -> int:
    """Start only the local admin screen for settings and prompt edits."""

    logger.info("AI Technical Lead admin screen started")

    settings = _load_settings_for_startup()
    if settings is not None:
        logger.info(
            "Target project root from settings: %s (current default is hardcoded above)",
            settings.project_root,
        )
    export_graph_diagrams(settings=settings)

    admin_host, admin_port = _admin_bind_address(settings)

    try:
        run_admin_server(admin_host, admin_port, SETTINGS_PATH)
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")

    return 0


def _parse_args() -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(description="Run the AI Tech Lead prototype.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("--reload", action="store_true", help="Hot-reload on source changes.")
    parser.add_argument("--admin", action="store_true", help="Run only the admin UI.")

    subparsers = parser.add_subparsers(dest="command")

    army_parser = subparsers.add_parser(
        "run-agent-task",
        help=(
            "JSON subprocess entry point: receive a coding task as JSON and "
            "return structured JSON output."
        ),
    )
    army_parser.add_argument(
        "--input-json",
        required=True,
        metavar="FILE",
        help="Path to JSON file containing the task request.",
    )
    army_parser.add_argument(
        "--output-json",
        required=True,
        metavar="FILE",
        help="Path to write the structured JSON result.",
    )

    subparsers.add_parser(
        "setup",
        help="Bootstrap the local workspace, settings, and runtime stores.",
    )
    subparsers.add_parser(
        "doctor",
        help="Report local workspace health and tracked runtime-file issues.",
    )
    bootstrap_pack_parser = subparsers.add_parser(
        "bootstrap-project-pack",
        help="Write a small starter AGENTS/docs/skills pack into a target repo.",
    )
    bootstrap_pack_parser.add_argument(
        "target_root",
        metavar="TARGET_ROOT",
        help="Target repository root to receive the starter project pack.",
    )
    bootstrap_pack_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting existing starter-pack files after review.",
    )
    subparsers.add_parser(
        "manifest",
        help="Print the machine-readable capability handshake for other agents.",
    )

    knowledge_parser = subparsers.add_parser(
        "knowledge-store",
        help="Inspect or maintain the configured local knowledge store.",
    )
    knowledge_subparsers = knowledge_parser.add_subparsers(dest="knowledge_command")
    knowledge_subparsers.required = True

    knowledge_subparsers.add_parser(
        "stats",
        help="Show a compact knowledge-store health summary.",
    )

    backup_parser = knowledge_subparsers.add_parser(
        "backup",
        help="Copy the knowledge store to a backup file.",
    )
    backup_parser.add_argument(
        "backup_path",
        metavar="BACKUP_PATH",
        help="Where to write the backup copy.",
    )

    restore_parser = knowledge_subparsers.add_parser(
        "restore",
        help="Restore the knowledge store from a backup file.",
    )
    restore_parser.add_argument(
        "backup_path",
        metavar="BACKUP_PATH",
        help="Backup file to restore from.",
    )

    knowledge_subparsers.add_parser(
        "compact",
        help="Compact the knowledge store in place.",
    )

    subparsers.add_parser(
        "backlog-sync-recover",
        help="Retry pending Google Sheets backlog updates that failed to sync.",
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
                    if _project_execution_active():
                        logger.info(
                            "Source change detected but coding agent is running; deferring restart."
                        )
                        deadline = time.time() + _RELOAD_AGENT_LOCK_WAIT_SECONDS
                        while _project_execution_active() and time.time() < deadline:
                            time.sleep(_RELOAD_POLL_INTERVAL_SECONDS)
                        if _project_execution_active():
                            logger.warning(
                                "Timed out waiting for coding agent to finish; restarting now."
                            )
                        current_snapshot = _reload_snapshot(watched_paths)
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


def _project_execution_active() -> bool:
    return bool(list_active_project_execution_locks())


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


def _run_setup() -> int:
    """Bootstrap the local runtime workspace."""

    try:
        for line in bootstrap_workspace():
            print(line)
    except Exception as error:
        logger.error("Workspace setup failed: %s", error)
        return 1
    return 0


def _run_doctor() -> int:
    """Print a compact workspace health report."""

    report = doctor_workspace()
    for line in report.lines:
        print(line)
    return 0 if report.ok else 1


def _run_bootstrap_project_pack(target_root: str, *, overwrite: bool) -> int:
    """Write a starter project pack into a reviewed target repo."""

    try:
        for line in bootstrap_project_pack(Path(target_root), overwrite=overwrite):
            print(line)
    except Exception as error:
        logger.error("Project-pack bootstrap failed: %s", error)
        return 1
    return 0


def _run_manifest() -> int:
    """Print the machine-readable capability handshake."""

    settings = _load_settings_for_startup()
    print(render_agent_manifest(settings, compact=True))
    return 0


def _run_knowledge_store(args: argparse.Namespace) -> int:
    """Run a knowledge-store maintenance subcommand."""

    settings = _load_settings_for_startup()
    if settings is None:
        return 1

    knowledge_store_path = _resolve_project_path(
        settings.project_root,
        settings.knowledge_store_path,
    )
    command = getattr(args, "knowledge_command", "")

    try:
        if command == "stats":
            stats = get_knowledge_store_statistics(knowledge_store_path)
            print(f"Path: {stats['path']}")
            print(f"Exists: {stats['exists']}")
            print(f"Items: {stats['item_count']}")
            print(f"Namespaces: {stats['namespace_count']}")
            print(f"Size bytes: {stats['size_bytes']}")
            return 0

        if command == "backup":
            backup_path = _resolve_project_path(settings.project_root, args.backup_path)
            result = backup_knowledge_store(knowledge_store_path, backup_path)
            print(f"Backed up knowledge store to {result}")
            return 0

        if command == "restore":
            backup_path = _resolve_project_path(settings.project_root, args.backup_path)
            result = restore_knowledge_store(backup_path, knowledge_store_path)
            print(f"Restored knowledge store from {backup_path} to {result}")
            return 0

        if command == "compact":
            result = compact_knowledge_store(knowledge_store_path)
            print(f"Compacted knowledge store: {result}")
            return 0

        logger.error("Unknown knowledge-store command: %s", command)
        return 1
    except Exception as error:
        logger.error("Knowledge-store command failed: %s", error)
        return 1


def _run_backlog_sync_recover() -> int:
    """Run a bounded recovery pass over pending Google Sheets backlog updates."""

    settings = _load_settings_for_startup()
    if settings is None:
        return 1

    from ai_tech_lead.backlog_runtime_store import run_pending_backlog_recovery

    outcomes = run_pending_backlog_recovery(settings)
    if not outcomes:
        print("No pending backlog updates.")
        return 0

    for status in outcomes:
        print(f"Pending backlog update: {status}")
    unresolved = sum(1 for status in outcomes if status != "synced")
    print(f"Processed {len(outcomes)} pending update(s); {unresolved} still need attention.")
    return 1 if unresolved else 0


def _resolve_project_path(project_root: str, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return Path(project_root) / path


if __name__ == "__main__":
    raise SystemExit(main())
