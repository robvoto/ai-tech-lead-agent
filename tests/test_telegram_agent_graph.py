from __future__ import annotations

import logging
from pathlib import Path

from helpers import valid_settings_dict
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from ai_tech_lead import telegram_agent_graph
from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.backlog_repository import MarkdownBacklogRepository
from ai_tech_lead.backlog_sheets_repository import BacklogSourceUnavailableError
from ai_tech_lead.telegram_agent_graph import (
    TelegramAgentReply,
    _build_backlog_tools,
    run_telegram_agent_message,
)
from ai_tech_lead.telegram_operator import TelegramCommand, TelegramCommandName, TelegramOperator


def _patch_markdown_backlog(monkeypatch, backlog_path: Path) -> None:
    """Point the live-repository factory at a Markdown fixture for this test.

    Production code always resolves the backlog through
    repository_from_settings (Google Sheets); tests inject a Markdown
    repository here to keep fixture-based test authoring without hitting
    real Sheets.
    """
    monkeypatch.setattr(
        "ai_tech_lead.telegram_agent_graph.repository_from_settings",
        lambda _settings: MarkdownBacklogRepository(backlog_path),
    )


def test_plain_text_uses_telegram_agent_graph_when_ai_enabled(monkeypatch, tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - First item\n\nGoal:\nDo the first thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)
    captured: dict[str, object] = {}

    def fake_run_telegram_agent_message(*, app, thread_id, text, session_cost_total_usd):
        captured["app"] = app
        captured["thread_id"] = thread_id
        captured["text"] = text
        captured["session_cost_total_usd"] = session_cost_total_usd
        return _Reply("Backlog has 1 item.")

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.build_telegram_agent_graph",
        lambda *, settings, checkpointer: "fake-app",
    )
    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.run_telegram_agent_message", fake_run_telegram_agent_message
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="how many backlog items?"),
        "demo-user",
    )

    assert captured["app"] == "fake-app"
    assert captured["thread_id"] == f"telegram-agent-{operator._session_id}-chat-1"
    assert captured["text"] == "how many backlog items?"
    assert captured["session_cost_total_usd"] == 0.0
    assert client.messages[-1] == ("chat-1", "Backlog has 1 item.")


def test_new_resets_telegram_agent_app() -> None:
    settings = parse_settings(valid_settings_dict())
    operator = TelegramOperator("token", settings, client=_RecordingClient())
    old_session_id = operator._session_id
    operator._telegram_agent_app = object()

    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert operator._session_id != old_session_id
    assert operator._telegram_agent_app is None


def test_telegram_agent_session_cost_accumulates_and_resets_on_new(
    monkeypatch,
) -> None:
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)
    operator = TelegramOperator("token", settings, client=_RecordingClient())
    seen_session_costs: list[float] = []
    replies = iter(
        [
            TelegramAgentReply(text="First reply.", usage_cost_usd=0.25),
            TelegramAgentReply(text="Second reply.", usage_cost_usd=0.50),
        ]
    )

    def fake_run_telegram_agent_message(*, app, thread_id, text, session_cost_total_usd):
        seen_session_costs.append(session_cost_total_usd)
        return next(replies)

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.run_telegram_agent_message",
        fake_run_telegram_agent_message,
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="first question"),
        "demo-user",
    )
    assert operator._telegram_agent_session_cost_usd == 0.25
    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="second question"),
        "demo-user",
    )
    assert operator._telegram_agent_session_cost_usd == 0.75
    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert seen_session_costs == [0.0, 0.25]
    assert operator._telegram_agent_session_cost_usd == 0.0


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
                        response_metadata={
                            "finish_reason": "stop",
                            "model_name": "gpt-4.1-mini-2025-04-14",
                        },
                        usage_metadata={
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    )
                ]
            }

    caplog.set_level(logging.INFO)
    run_telegram_agent_message(
        app=_FakeApp(),
        thread_id="thread-1",
        text="test",
        session_cost_total_usd=1.0,
    )

    assert "[LLM] telegram-chat" in caplog.text
    assert "tokens_total=120" in caplog.text
    assert "session:" in caplog.text
    assert "~$0.0001" in caplog.text


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
                            "token_usage": {
                                "prompt_tokens": 80,
                                "completion_tokens": 15,
                                "total_tokens": 95,
                            },
                        },
                    )
                ]
            }

    caplog.set_level(logging.INFO)
    run_telegram_agent_message(app=_FakeApp(), thread_id="thread-1", text="test")

    assert "[LLM] telegram-chat" in caplog.text
    assert "tokens_total=95" in caplog.text
    assert "session:" in caplog.text


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


def test_count_backlog_items_logs_learner_message(caplog, tmp_path: Path, monkeypatch) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - First item\n\nStatus: Backlog\n\nGoal:\nDo the first thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    _patch_markdown_backlog(monkeypatch, backlog_path)
    tools = _build_backlog_tools(settings)
    count_tool = next(tool for tool in tools if tool.name == "count_backlog_items")

    caplog.set_level(logging.INFO)
    result = count_tool.invoke({})

    assert result == "Backlog has 1 open item."
    assert "[LEARN] Backlog tool is counting backlog items." in caplog.text


def test_list_backlog_items_orders_and_shows_metadata(tmp_path: Path, monkeypatch) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-003 - Medium item\n\n"
        "Status: Done\n\n"
        "Priority: Medium\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-03\n"
        "Approval Required: no\n\n"
        "Goal:\nFinished.\n\n"
        "## ATL-001 - High review item\n\n"
        "Status: In Progress\n"
        "Priority: High\n"
        "Complexity: High\n"
        "Created Date: 2024-02-01\n"
        "Approval Required: yes\n\n"
        "Goal:\nDo the thing.\n\n"
        "## ATL-002 - High simple item\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-01\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the simpler thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    _patch_markdown_backlog(monkeypatch, backlog_path)
    tools = _build_backlog_tools(settings)
    list_tool = next(tool for tool in tools if tool.name == "list_backlog_items")

    result = list_tool.invoke({"limit": 10})

    assert result.splitlines() == [
        "ATL-002 - High simple item",
        "Priority: High | Complexity: Low | Created: 2024-01-01 | Approval: no | Status: Backlog",
        "ATL-001 - High review item",
        (
            "Priority: High | Complexity: High | Created: 2024-02-01 | "
            "Approval: yes | Status: In Progress"
        ),
    ]
    assert "ATL-003" not in result


def test_read_backlog_item_tool_returns_selected_item_details(
    tmp_path: Path, monkeypatch
) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Active item\n\n"
        "Status: In Progress\n\n"
        "Goal:\nDo the thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    _patch_markdown_backlog(monkeypatch, backlog_path)
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_backlog_item")

    result = read_tool.invoke({"item_id": "ATL-001"})

    assert result.startswith("ATL-001 - Active item")
    assert "Status: In Progress" in result
    assert "Do the thing." in result


def test_read_backlog_item_tool_returns_controlled_not_found_result(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Active item\n\nGoal:\nDo the thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    _patch_markdown_backlog(monkeypatch, backlog_path)
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_backlog_item")

    caplog.set_level(logging.WARNING)
    result = read_tool.invoke({"item_id": "ATL-999"})

    assert "ATL-999" in result
    assert "was not found" in result
    assert "Telegram backlog read failed" in caplog.text


def test_read_backlog_item_tool_returns_controlled_source_unavailable_result(
    monkeypatch, caplog
) -> None:
    class _UnavailableRepository:
        def get_item(self, item_id: str):
            raise BacklogSourceUnavailableError(f"backlog unavailable for {item_id}")

    settings = parse_settings(valid_settings_dict())
    monkeypatch.setattr(
        "ai_tech_lead.telegram_agent_graph.repository_from_settings",
        lambda _settings: _UnavailableRepository(),
    )
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_backlog_item")

    caplog.set_level(logging.WARNING)
    result = read_tool.invoke({"item_id": "ATL-001"})

    assert "ATL-001" in result
    assert "backlog unavailable" in result
    assert "Telegram backlog read failed" in caplog.text


def test_count_and_list_backlog_tools_return_controlled_source_errors(monkeypatch, caplog) -> None:
    class _UnavailableRepository:
        def list_open_items(self):
            raise BacklogSourceUnavailableError("backlog unavailable")

        def list_open_items_sorted(self):
            raise BacklogSourceUnavailableError("backlog unavailable")

    settings = parse_settings(valid_settings_dict())
    monkeypatch.setattr(
        "ai_tech_lead.telegram_agent_graph.repository_from_settings",
        lambda _settings: _UnavailableRepository(),
    )
    tools = _build_backlog_tools(settings)
    count_tool = next(tool for tool in tools if tool.name == "count_backlog_items")
    list_tool = next(tool for tool in tools if tool.name == "list_backlog_items")

    caplog.set_level(logging.WARNING)
    count_result = count_tool.invoke({})
    list_result = list_tool.invoke({})

    assert "Could not count backlog items" in count_result
    assert "Could not list backlog items" in list_result
    assert caplog.text.count("Telegram backlog tool failed") == 2


def test_build_telegram_agent_graph_passes_resolved_profile_to_chat_openai(monkeypatch) -> None:
    # telegram_chat is pinned to gpt-5.6-luna @ "none" via
    # execution_profiles.PURPOSE_PROFILE_OVERRIDE (ATL-090) regardless of
    # settings.orchestrator_ai_model — "none" is not just the benchmarked
    # cost/accuracy choice, it is the only reasoning effort that works with
    # this model when tools are bound over the Chat Completions endpoint.
    class _FakeLLM:
        def bind_tools(self, tools):
            return self

    settings = parse_settings(
        {**valid_settings_dict(), "orchestrator_ai_model": "gpt-4.1-mini"}
    )
    captured: dict[str, object] = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return _FakeLLM()

    monkeypatch.setattr(
        "ai_tech_lead.telegram_agent_graph.repository_from_settings",
        lambda _settings: MarkdownBacklogRepository(Path("/nonexistent/BACKLOG.md")),
    )
    monkeypatch.setattr(telegram_agent_graph, "ChatOpenAI", fake_chat_openai)

    telegram_agent_graph.build_telegram_agent_graph(settings=settings, checkpointer=MemorySaver())

    assert captured["model"] == "gpt-5.6-luna"
    assert captured["reasoning_effort"] == "none"
    assert captured["max_completion_tokens"] == 300


def test_telegram_agent_graph_survives_unexpected_tool_error(monkeypatch, caplog) -> None:
    class _BrokenRepository:
        def list_open_items(self):
            raise RuntimeError("simulated programming failure")

    class _FakeToolCallingLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                assert messages[-1].status == "error"
                assert "failed unexpectedly" in messages[-1].content
                return AIMessage(
                    content="The backlog tool failed, but the conversation is still available."
                )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "count_backlog_items",
                        "args": {},
                        "id": "call-count-backlog",
                        "type": "tool_call",
                    }
                ],
            )

    settings = parse_settings(valid_settings_dict())
    monkeypatch.setattr(
        "ai_tech_lead.telegram_agent_graph.repository_from_settings",
        lambda _settings: _BrokenRepository(),
    )
    monkeypatch.setattr(telegram_agent_graph, "ChatOpenAI", lambda **_kwargs: _FakeToolCallingLLM())
    caplog.set_level(logging.ERROR)

    app = telegram_agent_graph.build_telegram_agent_graph(
        settings=settings,
        checkpointer=MemorySaver(),
    )
    reply = run_telegram_agent_message(app=app, thread_id="thread-tool-error", text="count items")

    assert "conversation is still available" in reply.text
    assert "Unexpected Telegram tool exception" in caplog.text
    assert "count_backlog_items" in caplog.text
    assert "simulated programming failure" in caplog.text
    assert any(
        record.exc_info
        for record in caplog.records
        if "Unexpected Telegram tool exception" in record.message
    )


def test_set_backlog_item_status_tool_updates_repository(tmp_path: Path, monkeypatch) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Active item\n\n"
        "Status: Backlog\n\n"
        "Goal:\nDo the thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    _patch_markdown_backlog(monkeypatch, backlog_path)
    tools = _build_backlog_tools(settings)
    status_tool = next(tool for tool in tools if tool.name == "set_backlog_item_status")

    result = status_tool.invoke({"item_id": "ATL-001", "new_status": "Done"})

    assert "Updated ATL-001 - Active item" in result
    assert "Status is now 'Done'" in result
    assert "Status: Done" in backlog_path.read_text(encoding="utf-8")


def test_set_backlog_item_status_tool_accepts_wont_do(tmp_path: Path, monkeypatch) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - First item\n\n"
        "Status: Backlog\n\n"
        "Goal:\nDo the first thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    _patch_markdown_backlog(monkeypatch, backlog_path)

    tools = _build_backlog_tools(settings)
    status_tool = next(tool for tool in tools if tool.name == "set_backlog_item_status")

    result = status_tool.invoke({"item_id": "ATL-001", "new_status": "won't do"})

    assert "Status is now 'Won't Do'" in result
    assert "Status: Won't Do" in backlog_path.read_text(encoding="utf-8")


def test_read_project_file_tool_reads_allowed_file(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# Backlog\n", encoding="utf-8")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    arch_file = docs_dir / "ARCHITECTURE.md"
    arch_file.write_text("# Architecture\n\nThis is the architecture.", encoding="utf-8")

    raw_settings = valid_settings_dict()
    raw_settings["project_root"] = str(tmp_path)
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["allowed_directories"] = ["docs"]
    raw_settings["project_registry"] = [
        {
            "root": str(tmp_path),
            "name": tmp_path.name,
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]
    settings = parse_settings(raw_settings)
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_project_file")

    result = read_tool.invoke({"path": "docs/ARCHITECTURE.md"})

    assert "# Architecture" in result
    assert "This is the architecture." in result


def test_read_project_file_tool_rejects_path_outside_project_root(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# Backlog\n", encoding="utf-8")

    raw_settings = valid_settings_dict()
    raw_settings["project_root"] = str(tmp_path)
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["allowed_directories"] = ["docs"]
    raw_settings["project_registry"] = [
        {
            "root": str(tmp_path),
            "name": tmp_path.name,
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]
    settings = parse_settings(raw_settings)
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_project_file")

    result = read_tool.invoke({"path": "../../etc/passwd"})

    assert "Access denied" in result
    assert "outside the project root" in result


def test_read_project_file_tool_rejects_path_in_non_allowed_directory(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# Backlog\n", encoding="utf-8")
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "creds.txt").write_text("secret", encoding="utf-8")

    raw_settings = valid_settings_dict()
    raw_settings["project_root"] = str(tmp_path)
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["allowed_directories"] = ["docs"]
    raw_settings["project_registry"] = [
        {
            "root": str(tmp_path),
            "name": tmp_path.name,
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]
    settings = parse_settings(raw_settings)
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_project_file")

    result = read_tool.invoke({"path": "secrets/creds.txt"})

    assert "Access denied" in result
    assert "not in an allowed directory" in result


def test_read_project_file_tool_returns_error_for_missing_file(tmp_path: Path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# Backlog\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()

    raw_settings = valid_settings_dict()
    raw_settings["project_root"] = str(tmp_path)
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["allowed_directories"] = ["docs"]
    raw_settings["project_registry"] = [
        {
            "root": str(tmp_path),
            "name": tmp_path.name,
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]
    settings = parse_settings(raw_settings)
    tools = _build_backlog_tools(settings)
    read_tool = next(tool for tool in tools if tool.name == "read_project_file")

    result = read_tool.invoke({"path": "docs/MISSING.md"})

    assert "File not found" in result


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
