from __future__ import annotations

import pytest

from ai_tech_lead.telegram_input import (
    TelegramMessagePlaceholder,
    receive_telegram_message_placeholder,
)


def test_builds_structured_local_message() -> None:
    message = receive_telegram_message_placeholder(
        "  Create an execution brief  ",
        chat_id="  demo-chat  ",
        sender="  demo-user  ",
    )

    assert message == TelegramMessagePlaceholder(
        chat_id="demo-chat",
        sender="demo-user",
        text="Create an execution brief",
    )


def test_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="text cannot be empty"):
        receive_telegram_message_placeholder("   ")


def test_rejects_empty_metadata() -> None:
    with pytest.raises(ValueError, match="chat_id cannot be empty"):
        receive_telegram_message_placeholder("hello", chat_id=" ")

    with pytest.raises(ValueError, match="sender cannot be empty"):
        receive_telegram_message_placeholder("hello", sender=" ")
