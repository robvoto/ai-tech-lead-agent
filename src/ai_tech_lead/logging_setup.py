"""Project logging configuration helpers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ai_tech_lead.config import LOGS_DIR, ensure_project_dirs

LOG_FILE = LOGS_DIR / "ai_tech_lead.log"
LOGGER_NAME = "ai_tech_lead"


def configure_logging(*, debug: bool = False) -> logging.Logger:
    """Configure project logging for console and file output."""

    ensure_project_dirs()

    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    log_level = logging.DEBUG if debug else logging.INFO
    logger.setLevel(log_level)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=512_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        logging.Formatter("%(message)s")
    )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
