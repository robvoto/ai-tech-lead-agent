from __future__ import annotations

import logging
from dataclasses import replace
from urllib.error import URLError

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.backlog_draft_builder import BacklogRefinementBuildResult
from ai_tech_lead.backlog_repository import BacklogItem, BacklogRefinementDraft
from ai_tech_lead.backlog_status import BacklogStatus
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.telegram_agent_graph import TelegramAgentReply
from ai_tech_lead.telegram_operator import (
    CANONICAL_BOT_COMMANDS,
    ActiveTelegramTask,
    PendingBacklogDraft,
    TelegramApiClient,
    TelegramCommand,
    TelegramCommandName,
    TelegramOperator,
    TelegramTaskStage,
    _approval_prompt,
    _backlog_request_summary,
    _telegram_approval_reason,
    parse_telegram_command,
    parse_telegram_update,
)


def test_parse_run_command_with_backlog_id() -> None:
    command = parse_telegram_command("  /run ATL-001  ")

    assert command.name == TelegramCommandName.RUN
    assert command.argument == "ATL-001"


def test_parse_code_command_preserves_multiline_request() -> None:
    command = parse_telegram_command("/code Update docs\nAnd tests")

    assert command.name == TelegramCommandName.CODE
    assert command.argument == "Update docs\nAnd tests"


def test_parse_fix_command_is_not_supported() -> None:
    command = parse_telegram_command("/fix Update docs")

    assert command.name == TelegramCommandName.UNKNOWN
    assert command.raw_text == "/fix Update docs"


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

    assert reason == "AI risk review is off — manual approval required."


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
    assert "update docs only" in message
    assert "AI risk review is off" in message
    assert "manual approval required" in message
    assert "Telegram ad hoc request" not in message
    assert "Title:" not in message
    assert "Task:" not in message
    assert len(message.splitlines()) == 6
    assert message == (
        "Approval needed\n"
        "update docs only\n"
        "\n"
        "Risk: AI risk review is off — manual approval required.\n"
        "\n"
        "/approve or /reject"
    )


def test_approval_prompt_keeps_a_long_plan_readable() -> None:
    formulated_task = (
        "Review the research complexity check behavior in src/ai_tech_lead/research_checker.py "
        "to stop the orchestration safely when the LLM response is unavailable, invalid, or "
        "missing the required is_complex field. Implement a human-in-the-loop pause so the "
        "workflow does not silently continue when the classification step cannot be trusted."
    )

    message = _approval_prompt(
        task_label="ATL-022 - Add research safety gate",
        request_summary="Add research safety gate",
        approval_reason="Needs human approval.",
        formulated_task=formulated_task,
        limit=3900,
    )

    assert "Plan:" not in message
    assert "workflow does not silently continue" not in message
    assert "Risk: Needs human approval." in message
    assert message.endswith("/approve or /reject")


def test_parse_unknown_text_as_unknown_command() -> None:
    command = parse_telegram_command("hello there")

    assert command.name == TelegramCommandName.UNKNOWN


def test_rejects_invalid_run_command() -> None:
    with pytest.raises(ValueError, match="backlog item ID"):
        parse_telegram_command("/run not-an-id")


def test_parse_set_status_command_normalizes_allowed_statuses() -> None:
    command = parse_telegram_command("/set_status ATL-002 in progress")

    assert command.name == TelegramCommandName.SET_STATUS
    assert command.argument == "ATL-002 In Progress"


def test_rejects_invalid_set_status_command() -> None:
    command = parse_telegram_command("/set_status ATL-002 won't do")

    assert command.name == TelegramCommandName.SET_STATUS
    assert command.argument == "ATL-002 Won't Do"

    with pytest.raises(ValueError, match="1=Backlog, 2=Not Done, 3=In Progress"):
        parse_telegram_command("/set_status ATL-001 Almost Done")


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
    safe_operator = TelegramOperator(
        "token", settings, client=_FakeClient(), execute_coding_agent_override=False
    )
    enabled_operator = TelegramOperator(
        "token", settings, client=_FakeClient(), execute_coding_agent_override=True
    )

    assert "Telegram: ENABLED" in safe_operator._status_text("chat-1")
    assert "Coding-agent execution: DISABLED" in safe_operator._status_text("chat-1")
    assert "Coding-agent execution: ENABLED" in enabled_operator._status_text("chat-1")
    assert "Orchestrator AI model: gpt-4.1-mini." in safe_operator._status_text("chat-1")


def test_telegram_api_client_logs_request_context_on_network_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_opener(request, timeout):
        raise URLError(TimeoutError("_ssl.c:1015: The handshake operation timed out"))

    client = TelegramApiClient(
        "token",
        base_url="https://api.telegram.org",
        poll_timeout_seconds=25,
        opener=failing_opener,
    )

    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError, match="Telegram API request failed for getUpdates"):
        client.get_updates(timeout_seconds=25)

    assert (
        "Telegram API request failed method=getUpdates "
        "base_url=https://api.telegram.org timeout=35s"
    ) in caplog.text
    assert "payload_keys=allowed_updates,timeout" in caplog.text
    assert "_ssl.c:1015: The handshake operation timed out" in caplog.text


def test_help_text_shows_identity_and_model() -> None:
    settings = parse_settings(valid_settings_dict())
    operator = TelegramOperator("token", settings, client=_FakeClient())

    help_text = operator._help_text()
    lines = help_text.splitlines()

    assert "AI Technical Lead Orchestrator" in help_text
    assert "Purpose:" not in help_text
    assert "Role:" not in help_text
    assert "Orchestrator AI model: gpt-4.1-mini" in help_text
    assert "Orchestrator AI model: gpt-4.1-mini (disabled)." in help_text
    assert lines == [
        "AI Technical Lead Orchestrator",
        "Orchestrator AI model: gpt-4.1-mini (disabled).",
        "Commands:",
        "General:",
        "  /help - show command list",
        "  /commands - alias for /help",
        "  /status - check bot status",
        "Task control:",
        "  /new - start a fresh session and cancel current active task",
        "  /code <text> - explicit coding workflow",
        "  /cancel_code - stop the running coding-agent subprocess",
        "  /approve - approve the waiting task/decision",
        "  /reject - reject the waiting task/decision",
        "Backlog:",
        "  /propose <text> - create/refine a backlog draft from an idea",
        "  /run <backlog-id> - run backlog item, e.g. /run ATL-001",
        "  /next [limit] - show top backlog items by priority (default 3)",
        "  /list [limit|all] - list backlog items sorted by priority",
        "  /count - count backlog items",
        "  /read <backlog-id> - read backlog item details",
        (
            "  /set_status <backlog-id> <status|1-8> - propose backlog status "
            "update (1=Backlog 2=Not Done 3=In Progress 4=Needs Review "
            "5=Blocked 6=Done 7=Won't Do 8=Obsolete)"
        ),
    ]
    assert "/fix" not in help_text
    assert "/stop" not in help_text
    assert "/start - show this help" not in help_text


def test_command_handling_logs_action_details(caplog: pytest.LogCaptureFixture) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    caplog.set_level(logging.INFO)
    operator._handle_command(
        "chat-1", TelegramCommand(name=TelegramCommandName.STATUS), "demo-user"
    )
    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.HELP), "demo-user")
    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert "----------------------------------------" in caplog.text
    assert "Telegram action: showing status for chat chat-1." in caplog.text
    assert "Telegram action: showing help for chat chat-1." in caplog.text
    assert (
        f"Telegram action: resetting session in chat chat-1 for project root "
        f"{PROJECT_ROOT} (hardcoded default for now)." in caplog.text
    )
    assert (
        f"Telegram action: fresh session started for chat chat-1. Project root "
        f"{PROJECT_ROOT} (hardcoded default for now)." in caplog.text
    )


def test_cancel_code_command_logs_cleanup_state(caplog: pytest.LogCaptureFixture) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    class _FakeCancellationToken:
        def __init__(self, result: bool) -> None:
            self.result = result
            self.calls = 0

        def cancel(self) -> bool:
            self.calls += 1
            return self.result

    operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=object(),
        thread_config={"configurable": {"thread_id": "telegram-chat-1-demo"}},
        stage=TelegramTaskStage.RUNNING,
        cancellation_token=_FakeCancellationToken(True),
    )

    caplog.set_level(logging.INFO)
    operator._handle_command(
        "chat-1", TelegramCommand(name=TelegramCommandName.CANCEL_CODE), "demo-user"
    )

    assert "Telegram action: cancel_code requested in chat chat-1." in caplog.text
    assert (
        "Telegram action: evaluating running coding-agent cleanup for chat chat-1." in caplog.text
    )
    assert (
        "Telegram action: cancellation requested for chat chat-1; active task "
        "JH-001 - Local placeholder remains until the worker exits."
        in caplog.text
    )
    assert (
        client.messages[-1][1] == "Cancellation requested for the running coding-agent subprocess."
    )


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
        stage=TelegramTaskStage.PRE_RUN_APPROVAL,
    )

    old_session_id = operator._session_id
    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert "chat-1" not in operator._active_tasks
    assert operator._session_id != old_session_id
    assert client.messages[-1][0] == "chat-1"
    assert client.messages[-1][1].startswith("Fresh session ready.")
    assert "AI Technical Lead Orchestrator" not in client.messages[-1][1]
    assert "Purpose:" not in client.messages[-1][1]
    assert "Role:" not in client.messages[-1][1]


def test_new_command_logs_discarded_cleanup_details(caplog: pytest.LogCaptureFixture) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    class _FakeCancellationToken:
        def cancel(self) -> bool:
            return True

    operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=object(),
        thread_config={"configurable": {"thread_id": "telegram-chat-1-demo"}},
        stage=TelegramTaskStage.RUNNING,
        cancellation_token=_FakeCancellationToken(),
    )
    operator._pending_backlog_drafts["chat-1"] = PendingBacklogDraft(
        chat_id="chat-1",
        draft=BacklogRefinementDraft(
            item_id="ATL-002",
            title="Add backlog support",
            creator="Human",
            item_type="Story",
            epic="Backlog Management",
            priority="High",
            size="M",
            approval_required=True,
            approval_reason="Adds a backlog writing workflow.",
            problem="Rob needs a structured backlog workflow.",
            desired_outcome="Allow approved backlog drafts to be appended.",
            scope=["Collect rough ideas"],
            out_of_scope=["Build a full planning system"],
            acceptance_criteria=["The item captures research cache usage"],
            duplicate_check_result="No duplicate found.",
            stale_check_result="No stale item found.",
            already_done_check_result="Not already done.",
            research_required=True,
            research_cache_used=["docs/research/backlog-refinement-implementation-patterns.md"],
            external_research_needed=False,
            recommended_implementation_pattern="Use schema-validated structured output.",
            patterns_explicitly_rejected=["Freeform prose"],
            freshness_risk="Low.",
            implementation_guidance="Check the cache first and keep the item structured.",
            approval_risk_flags=["Touches backlog storage"],
        ),
    )

    caplog.set_level(logging.INFO)
    operator._handle_command("chat-1", TelegramCommand(name=TelegramCommandName.NEW), "demo-user")

    assert (
        "Telegram action: fresh session started for chat chat-1 after "
        "discarding active task JH-001 - Local placeholder, pending backlog "
        f"draft ATL-002. Project root {PROJECT_ROOT} "
        "(hardcoded default for now)."
        in caplog.text
    )
    assert "Reset Telegram agent memory and session id" in caplog.text
    assert "Discarded active task: JH-001 - Local placeholder." in client.messages[-1][1]
    assert "Discarded pending backlog draft: ATL-002." in client.messages[-1][1]
    assert (
        "Cancellation requested for the running coding-agent subprocess." in client.messages[-1][1]
    )


def test_run_returns_immediately_when_telegram_is_disabled() -> None:
    settings = parse_settings(valid_settings_dict())
    disabled_settings = replace(settings, telegram_enabled=False)
    operator = TelegramOperator("token", disabled_settings, client=_FakeClient())

    operator._run_polling = lambda: (_ for _ in ()).throw(
        AssertionError("polling should not start")
    )  # type: ignore[method-assign]
    operator._run_webhook = lambda: (_ for _ in ()).throw(
        AssertionError("webhook should not start")
    )  # type: ignore[method-assign]
    operator.run()


def test_run_graph_task_uses_requested_execution_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    client = FakeClient()

    class _ImmediateThread:
        def __init__(self, target, args, daemon):
            self._target = target
            self._args = args
            self.daemon = daemon

        def start(self) -> None:
            self._target(*self._args)

    def fake_build_graph(
        *,
        checkpointer_storage,
        execute_coding_agent_override,
        coding_agent_progress_callback=None,
        coding_agent_cancellation_token=None,
    ):
        captured["execute_coding_agent_override"] = execute_coding_agent_override
        captured["coding_agent_progress_callback"] = coding_agent_progress_callback
        captured["coding_agent_cancellation_token"] = coding_agent_cancellation_token
        return FakeApp()

    monkeypatch.setattr("ai_tech_lead.telegram_operator.build_graph", fake_build_graph)
    monkeypatch.setattr("ai_tech_lead.telegram_operator.threading.Thread", _ImmediateThread)

    operator = TelegramOperator(
        "token",
        settings,
        client=client,
        execute_coding_agent_override=True,
    )

    caplog.set_level(logging.INFO)
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
    assert callable(captured["coding_agent_progress_callback"])
    assert "----------------------------------------" in caplog.text
    assert "Telegram run: preparing graph task for chat chat-1." in caplog.text
    assert "Telegram run: task label: JH-001 - Local placeholder" in caplog.text
    assert "Telegram run: request summary: Local placeholder" in caplog.text
    assert "Telegram run: coding-agent execution override: True" in caplog.text
    assert "Telegram run: graph compiled and checkpointer attached." in caplog.text
    assert "Telegram run: starting background graph worker for chat chat-1." in caplog.text
    assert "Telegram run: background graph invocation started for chat chat-1." in caplog.text
    assert "Telegram run: background graph invocation finished for chat chat-1." in caplog.text
    assert "Telegram run: graph completed for chat chat-1; clearing active task." in caplog.text
    captured["coding_agent_progress_callback"]("Coding agent still running (about 1m 0s elapsed).")
    assert client.messages[-1] == (
        "chat-1",
        "Coding agent still running (about 1m 0s elapsed).",
    )
    assert operator._status_text("chat-1").startswith("Bot is alive.")


def test_plain_text_during_clarification_pause_stores_feedback_and_auto_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    app = _PausedTaskApp(
        {
            "request": "Backlog item: JH-001",
            "approval_reason": "Need one clarification.",
            "orchestrator_input_required": True,
            "orchestrator_input_question": "Please confirm the API path.",
            "orchestrator_input_reason": "The task needs a specific path before it can continue.",
            "task_feedback": [],
            "coding_agent_result": "Task done.",
        }
    )
    operator = TelegramOperator("token", settings, client=client)
    operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=app,
        thread_config={"configurable": {"thread_id": "telegram-chat-1-demo"}},
        stage=TelegramTaskStage.ORCHESTRATOR_INPUT,
    )

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.run_telegram_agent_message",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("telegram agent should not run while waiting")
        ),
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="Keep the current structure"),
        "demo-user",
    )

    assert app.update_calls == []
    assert len(app.invoke_calls) == 1
    # Graph resumed via Command(resume=text) — no state mutation before invoke
    invoke_args = app.invoke_calls[0][0]
    from langgraph.types import Command as _Command

    assert isinstance(invoke_args[0], _Command)
    assert invoke_args[0].resume == "Keep the current structure"
    # Graph completed (no next nodes), task was popped and completion message sent
    assert "chat-1" not in operator._active_tasks
    assert "Task complete:" in client.messages[-1][1]


def test_plain_text_during_clarification_pause_loops_when_new_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    next_question_state = {
        "request": "Backlog item: JH-001",
        "approval_reason": "Need clarification.",
        "orchestrator_input_required": True,
        "orchestrator_input_question": "Which branch should I target?",
        "orchestrator_input_reason": "Need branch name.",
        "task_feedback": [],
        "coding_agent_result": "",
    }
    app = _PausedTaskApp(
        next_question_state,
        next_nodes=("3c_clarification_interrupt",),
        interrupt_value={"kind": "clarification", "question": "Which branch should I target?"},
    )
    operator = TelegramOperator("token", settings, client=client)
    operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=app,
        thread_config={"configurable": {"thread_id": "telegram-chat-1-demo"}},
        stage=TelegramTaskStage.ORCHESTRATOR_INPUT,
    )

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.run_telegram_agent_message",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("telegram agent should not run while waiting")
        ),
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="Use the /api/v2 path"),
        "demo-user",
    )

    assert app.invoke_calls != []
    # Task still active — new question sent
    assert "chat-1" in operator._active_tasks
    assert operator._active_tasks["chat-1"].stage == TelegramTaskStage.ORCHESTRATOR_INPUT
    assert "Which branch should I target?" in client.messages[-1][1]


def test_plain_text_while_clarification_pending_appends_feedback_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # All plain text in ORCHESTRATOR_INPUT stage is treated as clarification input.
    # No heuristic classifies questions vs answers — the clarification checker decides.
    settings = parse_settings(valid_settings_dict())  # AI disabled
    client = _RecordingClient()
    app = _PausedTaskApp(
        {
            "request": "Backlog item: JH-001",
            "approval_reason": "Need one clarification.",
            "orchestrator_input_required": True,
            "orchestrator_input_question": "Please confirm the API path.",
            "task_feedback": [],
        }
    )
    operator = TelegramOperator("token", settings, client=client)
    operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=app,
        thread_config={"configurable": {"thread_id": "telegram-chat-1-demo"}},
        stage=TelegramTaskStage.ORCHESTRATOR_INPUT,
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="Why do you need this?"),
        "demo-user",
    )

    assert app.update_calls == []
    assert len(app.invoke_calls) == 1
    from langgraph.types import Command as _Command

    assert isinstance(app.invoke_calls[0][0][0], _Command)
    assert app.invoke_calls[0][0][0].resume == "Why do you need this?"


def test_approve_and_reject_still_work_for_waiting_task() -> None:
    settings = parse_settings(valid_settings_dict())

    approve_client = _RecordingClient()
    approve_app = _PausedTaskApp(
        {
            "request": "Backlog item: JH-001",
            "approval_reason": "Need approval.",
            "task_feedback": [],
            "coding_agent_result": "Task complete.",
        }
    )
    approve_operator = TelegramOperator("token", settings, client=approve_client)
    approve_operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=approve_app,
        thread_config={"configurable": {"thread_id": "telegram-chat-1-approve"}},
        stage=TelegramTaskStage.PRE_RUN_APPROVAL,
    )

    approve_operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.APPROVE),
        "demo-user",
    )

    assert approve_app.update_calls == []
    assert len(approve_app.invoke_calls) == 1
    from langgraph.types import Command as _Command

    approve_cmd = approve_app.invoke_calls[0][0][0]
    assert isinstance(approve_cmd, _Command)
    assert approve_cmd.resume == {"approved": True, "approved_by": "demo-user"}
    assert "chat-1" not in approve_operator._active_tasks
    assert approve_client.messages[-1][0] == "chat-1"
    assert approve_client.messages[-1][1].startswith("Task complete:")

    reject_client = _RecordingClient()
    reject_app = _PausedTaskApp(
        {
            "request": "Backlog item: JH-001",
            "approval_reason": "Need approval.",
            "task_feedback": [],
        }
    )
    reject_operator = TelegramOperator("token", settings, client=reject_client)
    reject_operator._active_tasks["chat-1"] = ActiveTelegramTask(
        chat_id="chat-1",
        task_label="JH-001 - Local placeholder",
        request_summary="Local placeholder",
        app=reject_app,
        thread_config={"configurable": {"thread_id": "telegram-chat-1-reject"}},
        stage=TelegramTaskStage.PRE_RUN_APPROVAL,
    )

    reject_operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.REJECT),
        "demo-user",
    )

    assert reject_app.update_calls == []
    assert reject_app.invoke_calls == []
    assert "chat-1" not in reject_operator._active_tasks
    assert reject_client.messages[-1] == (
        "chat-1",
        "Task rejected and closed: JH-001 - Local placeholder",
    )


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

    def delete_my_commands(self) -> None:
        return None

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        return None


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.messages.append((chat_id, text))

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        return None

    def delete_my_commands(self) -> None:
        return None

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        return None


class _PausedTaskApp:
    def __init__(
        self,
        state_values: dict[str, object],
        *,
        next_nodes: tuple[str, ...] = (),
        interrupt_value: dict[str, object] | None = None,
    ) -> None:
        self.state_values = state_values
        self.next_nodes = next_nodes
        self.interrupt_value = interrupt_value
        self.update_calls: list[dict[str, object]] = []
        self.invoke_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get_state(self, thread_config):
        interrupt_value = self.interrupt_value

        class _Interrupt:
            def __init__(self, value: object) -> None:
                self.value = value

        class _Task:
            def __init__(self, iv: object) -> None:
                self.interrupts = [_Interrupt(iv)] if iv is not None else []

        class Snapshot:
            def __init__(
                self, values: dict[str, object], next_nodes: tuple[str, ...], iv: object
            ) -> None:
                self.values = values
                self.next = next_nodes
                self.tasks = [_Task(iv)] if next_nodes else []

        return Snapshot(self.state_values, self.next_nodes, interrupt_value)

    def update_state(self, thread_config, values):
        self.update_calls.append(dict(values))
        self.state_values.update(values)

    def invoke(self, *args, **kwargs):
        self.invoke_calls.append((args, kwargs))
        return None


def test_plain_text_goes_to_telegram_agent_when_ai_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("dummy", settings, client=client)
    agent_called_with: list[str] = []

    def fake_run_telegram_agent_message(*, app, thread_id, text, session_cost_total_usd):
        agent_called_with.append(text)
        assert session_cost_total_usd == 0.0
        return TelegramAgentReply(text="There are 3 backlog items.")

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.run_telegram_agent_message",
        fake_run_telegram_agent_message,
    )
    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.build_telegram_agent_graph",
        lambda settings, checkpointer: object(),
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(
            name=TelegramCommandName.UNKNOWN,
            raw_text="How many backlog items?",
        ),
        "demo-user",
    )

    assert agent_called_with == ["How many backlog items?"]
    assert client.messages[-1] == ("chat-1", "There are 3 backlog items.")
    assert not any(msg[1].startswith("Bot is alive.") for msg in client.messages)


def test_plain_text_returns_fallback_when_ai_disabled() -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("dummy", settings, client=client)

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text="What is the status?"),
        "demo-user",
    )

    assert client.messages[-1][0] == "chat-1"
    assert "orchestrator AI is enabled" in client.messages[-1][1]
    assert not client.messages[-1][1].startswith("Bot is alive.")


def test_propose_command_creates_and_approves_backlog_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Existing item\n\nGoal:\nExisting\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    def fake_build_backlog_refinement_from_text(*, text, repository, settings):
        assert text == "add backlog support"
        return BacklogRefinementBuildResult(
            draft=BacklogRefinementDraft(
                item_id="ATL-002",
                title="Add backlog support",
                creator="Human",
                item_type="Story",
                epic="Backlog Management",
                priority="High",
                size="M",
                approval_required=True,
                approval_reason="Adds a backlog writing workflow.",
                problem="Rob needs a structured backlog workflow.",
                desired_outcome="Allow approved backlog drafts to be appended.",
                scope=["Collect rough ideas", "Refine them into structured items"],
                out_of_scope=["Build a full planning system"],
                acceptance_criteria=["The item captures research cache usage"],
                duplicate_check_result="No duplicate found.",
                stale_check_result="No stale item found.",
                already_done_check_result="Not already done.",
                research_required=True,
                research_cache_used=["docs/research/backlog-refinement-implementation-patterns.md"],
                external_research_needed=False,
                recommended_implementation_pattern="Use schema-validated structured output.",
                patterns_explicitly_rejected=["Freeform prose"],
                freshness_risk="Low.",
                implementation_guidance="Check the cache first and keep the item structured.",
                approval_risk_flags=["Touches backlog storage"],
            ),
            source="fake-model",
        )

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.build_backlog_refinement_from_text",
        fake_build_backlog_refinement_from_text,
    )

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.PROPOSE, argument="add backlog support"),
        "demo-user",
    )

    assert "Backlog refinement ready" in client.messages[-1][1]
    assert "ATL-002" in client.messages[-1][1]
    assert "Priority: High" in client.messages[-1][1]
    assert "Approval required: yes" in client.messages[-1][1]
    assert "Research required: yes" in client.messages[-1][1]

    operator._handle_command(
        "chat-1",
        TelegramCommand(name=TelegramCommandName.APPROVE),
        "demo-user",
    )

    assert client.messages[-1] == ("chat-1", "Backlog item added: ATL-002 - Add backlog support")
    text = backlog_path.read_text(encoding="utf-8")
    assert "Problem:" in text
    assert "Research Required: yes" in text


def test_parse_commands_alias_to_help() -> None:
    command = parse_telegram_command("/commands")

    assert command.name == TelegramCommandName.HELP
    assert command.argument == ""


def test_parse_new_with_extra_text_raises() -> None:
    with pytest.raises(ValueError, match="/propose"):
        parse_telegram_command("/new some idea")


def test_parse_propose_command() -> None:
    command = parse_telegram_command("/propose add search feature")

    assert command.name == TelegramCommandName.PROPOSE
    assert command.argument == "add search feature"


def test_parse_propose_without_text_raises() -> None:
    with pytest.raises(ValueError, match="/propose requires"):
        parse_telegram_command("/propose")


def test_parse_list_command_no_limit() -> None:
    command = parse_telegram_command("/list")

    assert command.name == TelegramCommandName.LIST
    assert command.argument == ""


def test_parse_list_command_with_limit() -> None:
    command = parse_telegram_command("/list 5")

    assert command.name == TelegramCommandName.LIST
    assert command.argument == "5"


def test_parse_list_command_invalid_limit_raises() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        parse_telegram_command("/list abc")


def test_parse_next_command_no_limit() -> None:
    command = parse_telegram_command("/next")

    assert command.name == TelegramCommandName.NEXT
    assert command.argument == ""


def test_parse_next_command_with_limit() -> None:
    command = parse_telegram_command("/next 5")

    assert command.name == TelegramCommandName.NEXT
    assert command.argument == "5"


def test_parse_next_command_invalid_limit_raises() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        parse_telegram_command("/next all")


def test_parse_count_command() -> None:
    command = parse_telegram_command("/count")

    assert command.name == TelegramCommandName.COUNT
    assert command.argument == ""


def test_parse_count_with_extra_text_raises() -> None:
    with pytest.raises(ValueError, match="does not accept"):
        parse_telegram_command("/count extra")


def test_parse_read_command() -> None:
    command = parse_telegram_command("/read ATL-001")

    assert command.name == TelegramCommandName.READ
    assert command.argument == "ATL-001"


def test_parse_read_without_id_raises() -> None:
    with pytest.raises(ValueError, match="backlog item ID"):
        parse_telegram_command("/read")


def test_parse_set_status_command() -> None:
    command = parse_telegram_command("/set_status ATL-001 Done")

    assert command.name == TelegramCommandName.SET_STATUS
    assert command.argument == "ATL-001 Done"


def test_parse_set_status_with_multi_word_status() -> None:
    command = parse_telegram_command("/set_status ATL-002 In Progress")

    assert command.name == TelegramCommandName.SET_STATUS
    assert command.argument == "ATL-002 In Progress"


def test_parse_set_status_missing_status_raises() -> None:
    with pytest.raises(ValueError, match="backlog ID and a status"):
        parse_telegram_command("/set_status ATL-001")


def test_commands_and_help_show_same_text() -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    recording_operator = TelegramOperator("token", settings, client=client)

    recording_operator._handle_command(
        "chat-1", TelegramCommand(name=TelegramCommandName.HELP), "demo-user"
    )
    help_message = client.messages[-1][1]

    recording_operator._handle_command(
        "chat-1",
        parse_telegram_command("/commands"),
        "demo-user",
    )
    commands_message = client.messages[-1][1]

    assert help_message == commands_message


def test_backlog_commands_bypass_ai_and_respond_directly() -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("dummy", settings, client=client)

    for cmd in ["/list", "/next", "/count"]:
        client.messages.clear()
        operator._handle_command("chat-1", parse_telegram_command(cmd), "demo-user")
        assert client.messages, f"{cmd} produced no reply"
        reply = client.messages[-1][1]
        assert "orchestrator AI" not in reply.lower() and "enabled" not in reply.lower()


def test_set_status_command_updates_backlog_item(tmp_path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - First item\n\n"
        "Status: Backlog\n\n"
        "Goal:\nDo the thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    operator._handle_command(
        "chat-1",
        parse_telegram_command("/set_status ATL-001 In Progress"),
        "demo-user",
    )

    assert client.messages[-1] == (
        "chat-1",
        "Updated ATL-001 - First item: Status is now 'In Progress'.",
    )
    assert "Status: In Progress" in backlog_path.read_text(encoding="utf-8")


def test_run_backlog_task_uses_explicit_item_and_starts_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)
    selected_item = BacklogItem(
        item_id="ATL-001",
        title="First item",
        body="Goal:\nDo the thing.\n",
        status=BacklogStatus.BACKLOG,
    )
    captured: dict[str, object] = {}

    def fake_load_backlog_item_by_id(task_id: str) -> BacklogItem:
        captured["task_id"] = task_id
        return selected_item

    def fake_run_graph_task(self, **kwargs) -> None:
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.load_backlog_item_by_id",
        fake_load_backlog_item_by_id,
    )
    monkeypatch.setattr(TelegramOperator, "_run_graph_task", fake_run_graph_task)

    operator._run_backlog_task("chat-1", "ATL-001")

    assert captured["task_id"] == "ATL-001"
    assert captured["kwargs"]["backlog_item_id"] == "ATL-001"
    assert captured["kwargs"]["task_label"] == "ATL-001 - First item"
    assert captured["kwargs"]["graph_state"]["request"].startswith("Backlog item: ATL-001")
    assert client.messages[-1][1].startswith("ATL-001 — First item")


def test_list_all_orders_by_priority_and_shows_item_metadata(tmp_path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-003 - Third item\n\n"
        "Status: Backlog\n"
        "Priority: Medium\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-03\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the medium thing.\n\n"
        "## ATL-002 - Second item\n\n"
        "Status: In Progress\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-03-01\n"
        "Approval Required: no\n\n"
        "Goal:\nKeep going.\n\n"
        "## ATL-001 - First item\n\n"
        "Status: Done\n"
        "Priority: High\n"
        "Complexity: High\n"
        "Created Date: 2024-02-01\n"
        "Approval Required: yes\n\n"
        "Goal:\nFinished.\n\n"
        "## ATL-004 - Fourth item\n\n"
        "Status: Done\n\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-01\n"
        "Approval Required: no\n\n"
        "Goal:\nOlder and simpler.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    operator._handle_command("chat-1", parse_telegram_command("/list all"), "demo-user")

    message = client.messages[-1][1]
    assert message.splitlines() == [
        "ATL-004 - Fourth item",
        "Priority: High | Complexity: Low | Created: 2024-01-01 | Approval: no | Status: Done",
        "",
        "ATL-002 - Second item",
        "Priority: High | Complexity: Low | Created: 2024-03-01 | Approval: no | Status: In Progress",
        "",
        "ATL-001 - First item",
        "Priority: High | Complexity: High | Created: 2024-02-01 | Approval: yes | Status: Done",
        "",
        "ATL-003 - Third item",
        "Priority: Medium | Complexity: Low | Created: 2024-01-03 | Approval: no | Status: Backlog",
    ]


def test_next_shows_top_three_ranked_items(tmp_path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-005 - High simple oldest\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-01\n"
        "Approval Required: no\n\n"
        "Goal:\nTop of the pile.\n\n"
        "## ATL-002 - High simple newer\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-02-01\n"
        "Approval Required: no\n\n"
        "Goal:\nStill simple.\n\n"
        "## ATL-001 - High review item\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: High\n"
        "Created Date: 2024-01-15\n"
        "Approval Required: yes\n\n"
        "Goal:\nNeeds review.\n\n"
        "## ATL-003 - Medium item\n\n"
        "Status: Backlog\n"
        "Priority: Medium\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-03\n"
        "Approval Required: no\n\n"
        "Goal:\nLower priority.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    operator._handle_command("chat-1", parse_telegram_command("/next"), "demo-user")

    assert client.messages[-1][1].splitlines() == [
        "1. ATL-005 - High simple oldest",
        "   Priority: High | Complexity: Low | Created: 2024-01-01 | Approval: no | Status: Backlog",
        "",
        "2. ATL-002 - High simple newer",
        "   Priority: High | Complexity: Low | Created: 2024-02-01 | Approval: no | Status: Backlog",
        "",
        "3. ATL-001 - High review item",
        "   Priority: High | Complexity: High | Created: 2024-01-15 | Approval: yes | Status: Backlog",
    ]


def test_list_all_splits_into_multiple_messages_when_needed(tmp_path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - First item with a title long enough to matter\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-01\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the first thing.\n\n"
        "## ATL-002 - Second item with a title long enough to matter\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-02\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the second thing.\n\n"
        "## ATL-003 - Third item with a title long enough to matter\n\n"
        "Status: Backlog\n"
        "Priority: High\n"
        "Complexity: Low\n"
        "Created Date: 2024-01-03\n"
        "Approval Required: no\n\n"
        "Goal:\nDo the third thing.\n",
        encoding="utf-8",
    )
    raw_settings = valid_settings_dict()
    raw_settings["backlog_path"] = str(backlog_path)
    raw_settings["telegram_max_message_chars"] = 180
    settings = parse_settings(raw_settings)
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    operator._handle_command("chat-1", parse_telegram_command("/list all"), "demo-user")

    assert len(client.messages) == 3
    assert client.messages[0][1].splitlines() == [
        "ATL-001 - First item with a title long enough to matter",
        "Priority: High | Complexity: Low | Created: 2024-01-01 | Approval: no | Status: Backlog",
    ]
    assert client.messages[1][1].splitlines() == [
        "ATL-002 - Second item with a title long enough to matter",
        "Priority: High | Complexity: Low | Created: 2024-01-02 | Approval: no | Status: Backlog",
    ]
    assert client.messages[2][1].splitlines() == [
        "ATL-003 - Third item with a title long enough to matter",
        "Priority: High | Complexity: Low | Created: 2024-01-03 | Approval: no | Status: Backlog",
    ]


def test_backlog_request_summary_returns_id_and_title_only() -> None:
    item = BacklogItem(
        item_id="ATL-001",
        title="First item",
        body=(
            "# Backlog\n\n"
            "## ATL-001 - First item\n\n"
            "Status: Backlog\n\n"
            "Goal:\n"
            "Do the thing.\n"
        ),
        status=BacklogStatus.BACKLOG,
    )

    summary = _backlog_request_summary(item)

    assert summary == "ATL-001 - First item"


def test_run_backlog_task_reports_done_items_to_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = parse_settings(valid_settings_dict())
    client = _RecordingClient()
    operator = TelegramOperator("token", settings, client=client)

    monkeypatch.setattr(
        "ai_tech_lead.telegram_operator.load_backlog_item_by_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Backlog item 'ATL-001' is Done and cannot be selected for execution.")
        ),
    )

    operator._run_backlog_task("chat-1", "ATL-001")

    assert (
        client.messages[-1][1]
        == "Backlog item 'ATL-001' is Done and cannot be selected for execution."
    )


def test_register_commands_payload_contains_only_canonical_commands() -> None:
    settings = parse_settings(valid_settings_dict())
    delete_calls: list[bool] = []
    set_calls: list[list[dict[str, str]]] = []

    class _CapturingClient:
        def send_message(self, chat_id: str, text: str) -> None:
            pass

        def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
            pass

        def delete_my_commands(self) -> None:
            delete_calls.append(True)

        def set_my_commands(self, commands: list[dict[str, str]]) -> None:
            set_calls.append(commands)

    operator = TelegramOperator("token", settings, client=_CapturingClient())
    operator._register_commands()

    assert delete_calls, "deleteMyCommands must be called before setMyCommands"
    assert len(set_calls) == 1
    registered = [c["command"] for c in set_calls[0]]
    expected = [cmd.name for cmd in CANONICAL_BOT_COMMANDS]
    assert registered == expected


def test_stale_openclaw_commands_are_not_registered() -> None:
    settings = parse_settings(valid_settings_dict())
    set_calls: list[list[dict[str, str]]] = []

    class _CapturingClient:
        def send_message(self, chat_id: str, text: str) -> None:
            pass

        def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
            pass

        def delete_my_commands(self) -> None:
            pass

        def set_my_commands(self, commands: list[dict[str, str]]) -> None:
            set_calls.append(commands)

    operator = TelegramOperator("token", settings, client=_CapturingClient())
    operator._register_commands()

    registered = {c["command"] for c in set_calls[0]}
    stale = {"fix", "stop", "restart", "reset", "reasoning"}
    assert not registered & stale, f"Stale commands found in payload: {registered & stale}"
