from __future__ import annotations

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.telegram_intent_router import (
    TelegramIntentAction,
    route_plain_text_intent,
)

from helpers import valid_settings_dict


def test_plain_text_router_falls_back_when_ai_disabled() -> None:
    settings = parse_settings(valid_settings_dict())

    intent = route_plain_text_intent(text="make the admin easier", settings=settings)

    assert intent.action == TelegramIntentAction.UNCLEAR
    assert "/code" in intent.response
    assert "implement a backlog item" in intent.response


def test_plain_text_router_uses_ai_when_enabled(monkeypatch) -> None:
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)

    class FakeResult:
        text = '{"action": "code_task", "summary": "Admin UI", "response": "This looks like a coding task. Use /code."}'

    monkeypatch.setattr(
        "ai_tech_lead.telegram_intent_router.call_orchestrator_llm",
        lambda prompt, config: FakeResult(),
    )

    intent = route_plain_text_intent(text="make the admin easier", settings=settings)

    assert intent.action == TelegramIntentAction.CODE_TASK
    assert intent.summary == "Admin UI"
    assert "/code" in intent.response
