from __future__ import annotations

from pathlib import Path

import pytest

from ai_tech_lead.telegram_operator import get_telegram_bot_token
from ai_tech_lead.telegram_secrets import (
    TelegramSecrets,
    load_telegram_secrets,
    save_telegram_secrets,
)


def test_telegram_secrets_round_trip(tmp_path: Path) -> None:
    secrets_path = tmp_path / "telegram_secrets.json"
    secrets = TelegramSecrets(bot_token="bot-token-value")

    save_telegram_secrets(secrets, secrets_path)

    loaded = load_telegram_secrets(secrets_path)

    assert loaded == secrets


def test_missing_telegram_secret_file_loads_as_blank(tmp_path: Path) -> None:
    secrets_path = tmp_path / "missing.json"

    assert load_telegram_secrets(secrets_path).bot_token == ""


def test_get_telegram_bot_token_reads_local_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secrets_path = tmp_path / "telegram_secrets.json"
    save_telegram_secrets(TelegramSecrets(bot_token="bot-token-value"), secrets_path)

    monkeypatch.setattr("ai_tech_lead.telegram_secrets.TELEGRAM_SECRETS_PATH", secrets_path)

    assert get_telegram_bot_token() == "bot-token-value"
