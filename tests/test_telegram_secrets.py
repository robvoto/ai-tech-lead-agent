from __future__ import annotations

import pytest

from ai_tech_lead.telegram_secrets import TELEGRAM_BOT_TOKEN_ENV, get_telegram_bot_token


def test_get_telegram_bot_token_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TELEGRAM_BOT_TOKEN_ENV, "bot-token-value")

    assert get_telegram_bot_token() == "bot-token-value"


def test_get_telegram_bot_token_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TELEGRAM_BOT_TOKEN_ENV, raising=False)
    monkeypatch.setattr("ai_tech_lead.telegram_secrets.load_local_env", lambda: None)

    with pytest.raises(RuntimeError, match=TELEGRAM_BOT_TOKEN_ENV):
        get_telegram_bot_token()
