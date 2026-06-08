"""Command-line entry point for the local AI Technical Lead Assistant."""

from __future__ import annotations

import argparse
import logging
from threading import Thread
from typing import Callable

from ai_tech_lead.admin_server import run_admin_server
from ai_tech_lead.app_settings import AppSettings, load_settings
from ai_tech_lead.config import PROJECT_ROOT, SETTINGS_PATH
from ai_tech_lead.logging_setup import LOGGER_NAME, configure_logging
from ai_tech_lead.telegram_operator import run_telegram_operator
from ai_tech_lead.storage import initialize_database

logger = logging.getLogger(LOGGER_NAME)


def main() -> None:
    """Initialize local storage and start the configured local services."""

    args = _parse_args()
    configure_logging(debug=args.debug)
    db_path = initialize_database()

    logger.info("AI Technical Lead Assistant started")
    logger.info("Project: %s", PROJECT_ROOT)
    logger.info("SQLite ready: %s", db_path)

    settings = _load_settings_for_startup()
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


def _parse_args() -> argparse.Namespace:
    """Parse debug-only command-line options."""

    parser = argparse.ArgumentParser(description="Run the AI Tech Lead prototype.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for local diagnostics.",
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
    main()
