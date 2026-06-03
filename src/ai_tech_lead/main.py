"""Command-line entry point for the local AI Technical Lead Assistant."""

import argparse
import logging

from ai_tech_lead.admin_server import DEFAULT_ADMIN_HOST, DEFAULT_ADMIN_PORT, run_admin_server
from ai_tech_lead.brief_graph_demo import run_sample_graph
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.logging_setup import LOGGER_NAME, configure_logging
from ai_tech_lead.storage import initialize_database

logger = logging.getLogger(LOGGER_NAME)


def main() -> None:
    """Initialize local storage and run the requested local workflow."""

    args = _parse_args()
    configure_logging()
    db_path = initialize_database()

    logger.info("AI Technical Lead Assistant started")
    logger.info("Project root detected: %s", PROJECT_ROOT)
    logger.info("SQLite ready: %s", db_path)

    if args.admin:
        run_admin_server(host=args.host, port=args.port)
        return

    run_sample_graph()


def _parse_args() -> argparse.Namespace:
    """Parse command-line options for local prototype workflows."""

    parser = argparse.ArgumentParser(description="Run the AI Tech Lead prototype.")
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Start the local coding-agent settings admin screen.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_ADMIN_HOST,
        help="Host for the admin screen.",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_ADMIN_PORT,
        type=int,
        help="Port for the admin screen.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
