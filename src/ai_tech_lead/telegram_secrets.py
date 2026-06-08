"""Local Telegram secret storage for the AI Tech Lead assistant."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ai_tech_lead.config import TELEGRAM_SECRETS_PATH


@dataclass(frozen=True)
class TelegramSecrets:
    """Local Telegram credentials stored outside tracked config."""

    bot_token: str


def load_telegram_secrets(secrets_path: Path | None = None) -> TelegramSecrets:
    """Load Telegram credentials from the local secret file."""

    secrets_path = secrets_path or TELEGRAM_SECRETS_PATH
    if not secrets_path.exists():
        return TelegramSecrets(bot_token="")

    with secrets_path.open(encoding="utf-8") as file:
        raw_secrets = json.load(file)

    if not isinstance(raw_secrets, dict):
        raise ValueError(f"Telegram secret file must contain a JSON object: {secrets_path}")

    return parse_telegram_secrets(raw_secrets)


def save_telegram_secrets(
    secrets: TelegramSecrets,
    secrets_path: Path | None = None,
) -> None:
    """Write Telegram credentials to the local secret file."""

    secrets_path = secrets_path or TELEGRAM_SECRETS_PATH
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(
        json.dumps(telegram_secrets_to_dict(secrets), indent=2) + "\n",
        encoding="utf-8",
    )


def parse_telegram_secrets(raw_secrets: dict[str, Any]) -> TelegramSecrets:
    """Validate Telegram credentials loaded from JSON."""

    bot_token = _optional_string(raw_secrets, "bot_token")
    return TelegramSecrets(bot_token=bot_token)


def telegram_secrets_to_dict(secrets: TelegramSecrets) -> dict[str, Any]:
    """Convert validated Telegram credentials into JSON-serializable data."""

    return {
        "bot_token": secrets.bot_token,
    }


def _optional_string(raw_secrets: dict[str, Any], key: str) -> str:
    value = raw_secrets.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Telegram secret '{key}' must be a string.")
    return value.strip()
