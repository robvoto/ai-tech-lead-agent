from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.telegram_agent_graph import _build_backlog_tools, run_telegram_agent_message
from ai_tech_lead.telegram_operator import TelegramCommand, TelegramCommandName, TelegramOperator

from helpers import valid_settings_dict


def test_plain_text_uses_telegram_agent_graph_when_ai_enabled(monkeypatch, tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - First item\n\n"
        "Goal:\nDo the first thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)
    captured: dict[str, object] = {}

    def fake_run_telegram_agent_message(*, app, thread_id, text):
        captured["app"] = app
        captured["thread_id"] = thread_id
        captured["text"] = text
        return _Reply("Backlog has 1 item.")

    monkeypatch.setattr("ai_tech_lead.telegram_operator.build_telegram_agent_graph", lambda *, settings, checkpointer: "fake-app")
    monkeypatch.setattr("ai_tech_lead.telegram_operator.run_telegram_agent_message", fake_run_telegram_agent_message)

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="how many backlog items?"),
        "demo-user",
    )

    assert captured["app"] == "fake-app"
    assert captured["thread_id"] == f"telegram-agent-{operator._session_id}-chat-1"
    assert captured["text"] == "how many backlog items?"
    assert client.messages[-1] == ("chat-1", "Backlog has 1 item.")


def test_new_resets_telegram_agent_memory() -> None:
    settings = parse_settings(valid_settings_dict())
    operator = TelegramOperator("token", settings, client=_RecordingClient())
    old_memory = operator._telegram_agent_memory
    operator._telegram_agent_app = object()

    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert operator._telegram_agent_memory is not old_memory
    assert operator._telegram_agent_app is None


def test_run_telegram_agent_message_logs_learner_steps(caplog) -> None:
    class _FakeApp:
        def invoke(self, graph_state, config):
            return {
                "messages": [
                    AIMessage(
                        content="Backlog has 1 item.",
                        response_metadata={"finish_reason": "stop"},
                    )
                ]
            }

    caplog.set_level(logging.INFO)

    reply = run_telegram_agent_message(app=_FakeApp(), thread_id="thread-1", text="how many items?")

    assert reply.text == "Backlog has 1 item."
    assert reply.truncated is False
    assert "[LEARN] Telegram message entered the LangGraph agent." in caplog.text
    assert "[LEARN] Assistant produced the final Telegram reply." in caplog.text


def test_run_telegram_agent_message_logs_token_usage(caplog) -> None:
    class _FakeApp:
        def invoke(self, graph_state, config):
            return {
                "messages": [
                    AIMessage(
                        content="Answer.",
                        response_metadata={"finish_reason": "stop", "model_name": "gpt-4.1-mini-2025-04-14"},
                        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                    )
                ]
            }

    caplog.set_level(logging.INFO)
    run_telegram_agent_message(app=_FakeApp(), thread_id="thread-1", text="test")

    assert "[LLM] telegram-chat" in caplog.text
    assert "tokens_total=120" in caplog.text
    assert "cost_total=~$0.0001" in caplog.text


def test_run_telegram_agent_message_falls_back_to_response_metadata_tokens(caplog) -> None:
    class _FakeApp:
        def invoke(self, graph_state, config):
            return {
                "messages": [
                    AIMessage(
                        content="Answer.",
                        response_metadata={
                            "finish_reason": "stop",
                            "model_name": "gpt-4.1-mini-2025-04-14",
                            "token_usage": {"prompt_tokens": 80, "completion_tokens": 15, "total_tokens": 95},
                        },
                    )
                ]
            }

    caplog.set_level(logging.INFO)
    run_telegram_agent_message(app=_FakeApp(), thread_id="thread-1", text="test")

    assert "[LLM] telegram-chat" in caplog.text
    assert "tokens_total=95" in caplog.text
    assert "cost_total=~$" in caplog.text


def test_run_telegram_agent_message_skips_usage_log_when_no_usage(caplog) -> None:
    class _FakeApp:
        def invoke(self, graph_state, config):
            return {
                "messages": [
                    AIMessage(
                        content="Answer.",
                        response_metadata={"finish_reason": "stop"},
                    )
                ]
            }

    caplog.set_level(logging.INFO)
    run_telegram_agent_message(app=_FakeApp(), thread_id="thread-1", text="test")

    assert "[LLM]" not in caplog.text


def test_count_backlog_items_logs_learner_message(caplog, tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - First item\n\n"
        "Goal:\nDo the first thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    tools = _build_backlog_tools(settings)
    count_tool = next(tool for tool in tools if tool.name == "count_backlog_items")

    caplog.set_level(logging.INFO)
    result = count_tool.invoke({})

    assert result == "Backlog has 1 items."
    assert "[LEARN] Backlog tool is counting backlog items." in caplog.text


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        return None
