"""Telegram token loading from the local environment."""

from __future__ import annotations

import os

from ai_tech_lead.env_loader import load_local_env

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


def get_telegram_bot_token() -> str:
    """Read the Telegram bot token from the environment."""

    load_local_env()
    token = os.environ.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError("Telegram bot token is required in TELEGRAM_BOT_TOKEN.")
    return token
