from __future__ import annotations

import json

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.telegram_intent_router import (
    TelegramIntentAction,
    route_plain_text_intent,
)


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
        text = (
            '{"action": "code_task", "summary": "Admin UI", '
            '"response": "This looks like a coding task. Use /code."}'
        )

    monkeypatch.setattr(
        "ai_tech_lead.telegram_intent_router.call_orchestrator_llm",
        lambda prompt, config: FakeResult(),
    )

    intent = route_plain_text_intent(text="make the admin easier", settings=settings)

    assert intent.action == TelegramIntentAction.CODE_TASK
    assert intent.summary == "Admin UI"
    assert "/code" in intent.response


@pytest.mark.parametrize(
    ("action", "summary", "response"),
    [
        (
            TelegramIntentAction.BACKLOG_PROPOSAL,
            "Add backlog item",
            "Use /propose to capture that work as a backlog draft.",
        ),
        (
            TelegramIntentAction.STATUS,
            "Change backlog state",
            "Use /set_status when you want to change a backlog item state.",
        ),
        (
            TelegramIntentAction.ASK,
            "Ask about the backlog",
            "This is a question, so I can answer or explain the workflow.",
        ),
    ],
)
def test_plain_text_router_parses_multiple_ai_intents(
    monkeypatch, action: TelegramIntentAction, summary: str, response: str
) -> None:
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)

    class FakeResult:
        def __init__(self, text: str) -> None:
            self.text = text

    monkeypatch.setattr(
        "ai_tech_lead.telegram_intent_router.call_orchestrator_llm",
        lambda prompt, config: FakeResult(
            json.dumps(
                {
                    "action": action.value,
                    "summary": summary,
                    "response": response,
                }
            )
        ),
    )

    intent = route_plain_text_intent(text="please handle this request", settings=settings)

    assert intent.action == action
    assert intent.summary == summary
    assert intent.response == response
