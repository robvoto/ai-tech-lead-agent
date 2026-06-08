from __future__ import annotations

from dataclasses import replace

import pytest

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.telegram_intent_router import TelegramIntent, TelegramIntentAction
from ai_tech_lead.telegram_operator import (
    ActiveTelegramTask,
    TelegramCommand,
    TelegramCommandName,
    TelegramOperator,
    _approval_prompt,
    _telegram_approval_reason,
    parse_telegram_command,
    parse_telegram_update,
)

from helpers import valid_settings_dict


def test_parse_run_command_with_backlog_id() -> None:
    command = parse_telegram_command("  /run ATL-001  ")

    assert command.name == TelegramCommandName.RUN
    assert command.argument == "ATL-001"


def test_parse_code_command_preserves_multiline_request() -> None:
    command = parse_telegram_command("/code Update docs\nAnd tests")

    assert command.name == TelegramCommandName.CODE
    assert command.argument == "Update docs\nAnd tests"


def test_parse_fix_command_remains_code_alias() -> None:
    command = parse_telegram_command("/fix Update docs")

    assert command.name == TelegramCommandName.FIX
    assert command.argument == "Update docs"


def test_parse_status_command() -> None:
    command = parse_telegram_command("/status")

    assert command.name == TelegramCommandName.STATUS
    assert command.argument == ""


def test_parse_help_aliases_start_and_help() -> None:
    assert parse_telegram_command("/start").name == TelegramCommandName.HELP
    assert parse_telegram_command("/help").name == TelegramCommandName.HELP


def test_parse_new_command_accepts_no_extra_text() -> None:
    command = parse_telegram_command("/new")

    assert command.name == TelegramCommandName.NEW
    assert command.argument == ""


def test_telegram_approval_reason_handles_current_ai_off_text() -> None:
    reason = _telegram_approval_reason(
        "AI risk review is off, so I need your approval before continuing. "
        "Task: Telegram ad hoc request Title: update docs only update docs only"
    )

    assert reason == "AI risk review is off, so I need your approval before continuing."


def test_approval_prompt_keeps_telegram_message_human() -> None:
    message = _approval_prompt(
        task_label="Ad hoc fix - update docs only",
        request_summary="update docs only",
        approval_reason=(
            "AI risk review is off, so I need your approval before continuing. "
            "Task: Telegram ad hoc request Title: update docs only update docs only"
        ),
        limit=3900,
    )

    assert "Approval needed" in message
    assert "Task: update docs only" in message
    assert "AI risk review is off" in message
    assert "before continuing" in message
    assert "Telegram ad hoc request" not in message
    assert "Title:" not in message
    assert "Request:" not in message
    assert len(message.splitlines()) == 4
    assert message == (
        "Approval needed\n"
        "Task: update docs only\n"
        "Why: AI risk review is off, so I need your approval before continuing.\n"
        "Reply /approve or /reject."
    )


def test_parse_unknown_text_as_unknown_command() -> None:
    command = parse_telegram_command("hello there")

    assert command.name == TelegramCommandName.UNKNOWN


def test_rejects_invalid_run_command() -> None:
    with pytest.raises(ValueError, match="backlog item ID"):
        parse_telegram_command("/run not-an-id")


def test_parse_update_extracts_chat_and_sender() -> None:
    update = parse_telegram_update(
        {
            "update_id": 5,
            "message": {
                "text": "/status",
                "chat": {"id": 42},
                "from": {"username": "demo-user", "first_name": "Demo"},
            },
        }
    )

    assert update is not None
    assert update.chat_id == "42"
    assert update.sender == "demo-user"


def test_status_text_reflects_execution_mode() -> None:
    settings = parse_settings(valid_settings_dict())
    safe_operator = TelegramOperator("token", settings, client=_FakeClient(), execute_coding_agent_override=False)
    enabled_operator = TelegramOperator("token", settings, client=_FakeClient(), execute_coding_agent_override=True)

    assert "Telegram: ENABLED" in safe_operator._status_text("chat-1")
    assert "Coding-agent execution: DISABLED" in safe_operator._status_text("chat-1")
    assert "Coding-agent execution: ENABLED" in enabled_operator._status_text("chat-1")
    assert "Orchestrator AI model: gpt-4.1-mini." in safe_operator._status_text("chat-1")


def test_help_text_shows_identity_and_model() -> None:
    settings = parse_settings(valid_settings_dict())
    operator = TelegramOperator("token", settings, client=_FakeClient())

    help_text = operator._help_text()

    assert "AI Technical Lead Orchestrator" in help_text
    assert "Orchestrator AI model: gpt-4.1-mini" in help_text
    assert "/help - show this help" in help_text
    assert "/code <text> - explicit coding workflow" in help_text
    assert "/fix <text> - old alias for /code" in help_text
    assert "/start - show this help" not in help_text


def test_new_command_discards_active_task_and_resets_session() -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)
    operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=object(),
        thread_config={"configurable": {"thread_id": "telegram-chat-1-demo"}},
    )

    old_session_id = operator._session_id
    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert "chat-1" not in operator._active_tasks
    assert operator._session_id != old_session_id
    assert client.messages[-1][0] == "chat-1"
    assert client.messages[-1][1].startswith("Fresh session ready.")
    assert "AI Technical Lead Orchestrator" in client.messages[-1][1]


def test_run_returns_immediately_when_telegram_is_disabled() -> None:
    settings = parse_settings(valid_settings_dict())
    disabled_settings = replace(settings, telegram_enabled=False)
    operator = TelegramOperator("token", disabled_settings, client=_FakeClient())

    operator._run_polling = lambda: (_ for _ in ()).throw(AssertionError("polling should not start"))  # type: ignore[method-assign]
    operator._run_webhook = lambda: (_ for _ in ()).throw(AssertionError("webhook should not start"))  # type: ignore[method-assign]
    operator.run()


def test_run_graph_task_uses_requested_execution_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = parse_settings(valid_settings_dict())
    captured: dict[str, object] = {}

    class FakeApp:
        def invoke(self, graph_state, config):
            captured["graph_state"] = graph_state
            captured["config"] = config

        def get_state(self, thread_config):
            class Snapshot:
                next: tuple[str, ...] = ()
                values = {"coding_agent_result": "disabled"}

            return Snapshot()

    class FakeClient:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def send_message(self, chat_id: str, text: str) -> None:
            self.messages.append((chat_id, text))

        def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
            return None

    def fake_build_graph(*, checkpointer_storage, execute_coding_agent_override):
        captured["execute_coding_agent_override"] = execute_coding_agent_override
        return FakeApp()

    monkeypatch.setattr("ai_tech_lead.telegram_operator.build_graph", fake_build_graph)

    operator = TelegramOperator(
        "token",
        settings,
        client=FakeClient(),
        execute_coding_agent_override=True,
    )

    operator._run_graph_task(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        graph_state={
            "request": "Backlog item: JH-001",
            "brief": "",
            "needs_approval": False,
            "approval_reason": "Risk review has not run yet.",
            "approved": False,
            "agent_instruction": "",
            "coding_agent_result": "",
        },
    )

    assert captured["execute_coding_agent_override"] is True
    assert operator._status_text("chat-1").startswith("Bot is alive.")


def test_telegram_operator_allows_multiple_chats_when_configured() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["telegram_allowed_chat_ids"] = ["1", "2"]
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    assert operator._is_chat_allowed("1") is True
    assert operator._is_chat_allowed("2") is True
    assert operator._is_chat_allowed("3") is False
    assert client.messages[-1] == ("3", "This chat is not allowed to use the bot.")


class _FakeClient:
    def send_message(self, chat_id: str, text: str) -> None:
        raise AssertionError("send_message should not be called")

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        return None


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        return None



def test_plain_text_routes_through_intent_router(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("dummy", settings, client=client)

    def fake_route_plain_text_intent(*, text, settings):
        assert text == "make the admin easier to use"
        return TelegramIntent(
            action=TelegramIntentAction.CODE_TASK,
            summary="Admin UI improvement",
            response="This looks like a coding task. Use /code to start the coding workflow.",
        )

    monkeypatch.setattr("ai_tech_lead.telegram_operator.route_plain_text_intent", fake_route_plain_text_intent)

    operator._handle_command(
        "chat-1",
        TelegramCommand(
            name=TelegramCommandName.UNKNOWN,
            raw_text="make the admin easier to use",
        ),
        "demo-user",
    )

    assert client.messages[-1] == (
        "chat-1",
        "This looks like a coding task. Use /code to start the coding workflow.",
    )


def test_plain_text_status_intent_returns_status(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("dummy", settings, client=client)

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.route_plain_text_intent",
        lambda text, settings: TelegramIntent(
            action=TelegramIntentAction.STATUS,
            summary="Status request",
            response="",
        ),
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="are you running?"),
        "demo-user",
    )

    assert client.messages[-1][0] == "chat-1"
    assert client.messages[-1][1].startswith("Bot is alive.")
