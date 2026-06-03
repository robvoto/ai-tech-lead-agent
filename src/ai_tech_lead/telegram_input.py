"""Local-only Telegram input placeholder.

This module deliberately does not connect to Telegram or read secrets. It gives
future integration work a small, testable shape for a received message.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramMessagePlaceholder:
    """A locally supplied stand-in for a future Telegram message."""

    chat_id: str
    sender: str
    text: str


def receive_telegram_message_placeholder(
    text: str,
    *,
    chat_id: str = "local-demo-chat",
    sender: str = "local-demo-user",
) -> TelegramMessagePlaceholder:
    """Create a local placeholder message without any Telegram API access."""

    normalized_text = text.strip()
    normalized_chat_id = chat_id.strip()
    normalized_sender = sender.strip()

    if not normalized_text:
        raise ValueError("Telegram placeholder text cannot be empty.")
    if not normalized_chat_id:
        raise ValueError("Telegram placeholder chat_id cannot be empty.")
    if not normalized_sender:
        raise ValueError("Telegram placeholder sender cannot be empty.")

    return TelegramMessagePlaceholder(
        chat_id=normalized_chat_id,
        sender=normalized_sender,
        text=normalized_text,
    )
