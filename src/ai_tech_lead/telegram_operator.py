"""Telegram adapter for the local AI Tech Lead workflow.

This module keeps the Telegram surface deliberately small:
- polling and webhook transports
- one active task per chat
- explicit command handling
- no hidden task selection
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import logging
import re
import uuid
from collections.abc import Mapping
from typing import Any, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from .app_settings import AppSettings, load_settings
from .backlog_draft_builder import BacklogDraftBuildError, build_backlog_draft_from_text
from .backlog_loader import backlog_item_to_graph_state, load_backlog_item_by_id
from .coding_workflow_graph import GraphState, build_graph
from .config import PROJECT_ROOT
from .logging_setup import LOGGER_NAME
from .backlog_repository import BacklogDraft, MarkdownBacklogRepository, render_backlog_draft
from .telegram_agent_graph import build_telegram_agent_graph, run_telegram_agent_message
from .telegram_secrets import load_telegram_secrets

logger = logging.getLogger(LOGGER_NAME)
ORCHESTRATOR_IDENTITY_PATH = PROJECT_ROOT / "docs" / "ORCHESTRATOR_IDENTITY.md"


class TelegramCommandName(StrEnum):
    """Supported Telegram command names."""

    HELP = "help"
    NEW = "new"
    RUN = "run"
    CODE = "code"
    FIX = "fix"
    STATUS = "status"
    STOP = "stop"
    APPROVE = "approve"
    REJECT = "reject"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TelegramCommand:
    """Parsed Telegram command text."""

    name: TelegramCommandName
    argument: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class TelegramUpdate:
    """Relevant fields extracted from a Telegram update."""

    update_id: int
    chat_id: str
    text: str
    sender: str


@dataclass
class PendingBacklogDraft:
    """Backlog draft waiting for explicit Telegram approval."""

    chat_id: str
    draft: BacklogDraft


@dataclass
class ActiveTelegramTask:
    """Single in-flight Telegram task.

    The bot only supports one active task per chat so approval remains simple.
    """

    chat_id: str
    task_label: str
    request_summary: str
    app: Any
    thread_config: RunnableConfig


class TelegramApiClient:
    """Minimal Telegram Bot API client using long polling."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str,
        poll_timeout_seconds: int,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise ValueError("Telegram bot token cannot be empty.")

        self._token = normalized_token
        self._base_url = base_url.rstrip("/")
        self._poll_timeout_seconds = poll_timeout_seconds
        self._opener = opener

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_seconds: int,
    ) -> list[TelegramUpdate]:
        """Fetch long-polling updates from Telegram."""

        payload: dict[str, object] = {
            "timeout": timeout_seconds,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            payload["offset"] = offset

        response = self._post("getUpdates", payload)
        updates: list[TelegramUpdate] = []

        for update_data in response.get("result", []):
            update = parse_telegram_update(update_data)
            if update is not None:
                updates.append(update)

        return updates

    def set_webhook(
        self,
        url: str,
        *,
        secret_token: str | None = None,
        drop_pending_updates: bool = False,
    ) -> None:
        """Register a webhook endpoint for the bot."""

        payload: dict[str, object] = {
            "url": url,
            "drop_pending_updates": _bool_text(drop_pending_updates),
        }
        if secret_token:
            payload["secret_token"] = secret_token
        self._post("setWebhook", payload)

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        """Remove the webhook endpoint before switching back to polling."""

        self._post(
            "deleteWebhook",
            {"drop_pending_updates": _bool_text(drop_pending_updates)},
        )

    def send_message(self, chat_id: str, text: str) -> None:
        """Send a plain text message to one Telegram chat."""

        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        self._post("sendMessage", payload)

    def _post(self, method: str, payload: Mapping[str, object]) -> dict[str, Any]:
        url = f"{self._base_url}/bot{self._token}/{method}"
        encoded_payload = urlencode(payload).encode("utf-8")
        request = Request(url, data=encoded_payload, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with self._opener(request, timeout=self._poll_timeout_seconds + 10) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API error calling {method}: {error_body}") from error
        except URLError as error:
            raise RuntimeError(f"Telegram API request failed for {method}: {error.reason}") from error

        data = json.loads(body)
        if not isinstance(data, dict):
            raise RuntimeError(f"Telegram API returned invalid JSON for {method}.")
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram API call failed for {method}: {data}")
        return data


class TelegramOperator:
    """Run the coding workflow from Telegram with one active task per chat."""

    def __init__(
        self,
        token: str,
        settings: AppSettings,
        *,
        client: TelegramApiClient | None = None,
        execute_coding_agent_override: bool | None = None,
        shutdown_callback: Callable[[], None] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or TelegramApiClient(
            token,
            base_url=settings.telegram_api_base_url,
            poll_timeout_seconds=settings.telegram_long_poll_timeout_seconds,
        )
        self._execute_coding_agent_override = execute_coding_agent_override
        self._shutdown_callback = shutdown_callback
        self._running = False
        self._active_tasks: dict[str, ActiveTelegramTask] = {}
        self._pending_backlog_drafts: dict[str, PendingBacklogDraft] = {}
        self._telegram_agent_memory = MemorySaver()
        self._telegram_agent_app: Any | None = None
        self._session_id = uuid.uuid4().hex
        self._allowed_chat_ids = {
            chat_id.strip() for chat_id in settings.telegram_allowed_chat_ids if chat_id.strip()
        }

    def run(self) -> None:
        """Run the configured Telegram transport until /stop or shutdown."""

        if not self._settings.telegram_enabled:
            logger.info("Telegram operator disabled by settings.")
            return

        transport = self._settings.telegram_transport
        logger.info("Telegram operator starting (%s transport)", transport)

        if transport == "webhook":
            self._run_webhook()
            return

        self._run_polling()

    def _run_polling(self) -> None:
        logger.info("Telegram polling mode selected")
        self._running = True
        offset: int | None = None

        try:
            self._client.delete_webhook(drop_pending_updates=False)
        except Exception:
            logger.exception("Unable to clear webhook before starting polling mode")
            raise

        while self._running:
            updates = self._client.get_updates(
                offset=offset,
                timeout_seconds=self._settings.telegram_long_poll_timeout_seconds,
            )
            for update in updates:
                offset = update.update_id + 1
                self._handle_update(update)

        logger.info("Telegram operator stopped")

    def _run_webhook(self) -> None:
        logger.info("Telegram webhook mode selected")

        webhook_url = self._settings.telegram_webhook_url
        parsed_url = urlparse(webhook_url)
        if parsed_url.scheme not in {"http", "https"}:
            raise ValueError("telegram_webhook_url must start with http:// or https://")
        if not parsed_url.path:
            raise ValueError("telegram_webhook_url must include a webhook path.")

        handler_class = self._build_webhook_handler(
            expected_path=parsed_url.path,
            secret_token=self._settings.telegram_webhook_secret_token,
        )
        server = ThreadingHTTPServer(
            (
                self._settings.telegram_webhook_bind_host,
                self._settings.telegram_webhook_bind_port,
            ),
            handler_class,
        )
        self._set_shutdown_callback(server.shutdown)

        try:
            self._client.set_webhook(
                webhook_url,
                secret_token=self._settings.telegram_webhook_secret_token,
                drop_pending_updates=False,
            )
            logger.info(
                "Telegram webhook listening on %s:%s for %s",
                self._settings.telegram_webhook_bind_host,
                self._settings.telegram_webhook_bind_port,
                parsed_url.path,
            )
            server.serve_forever()
        finally:
            try:
                self._client.delete_webhook(drop_pending_updates=False)
            except Exception:
                logger.exception("Failed to delete Telegram webhook during shutdown")
            server.server_close()
            logger.info("Telegram webhook server stopped")

    def _handle_update(self, update: TelegramUpdate) -> None:
        try:
            command = parse_telegram_command(update.text)
            self._handle_command(update.chat_id, command, update.sender)
        except ValueError as error:
            logger.info("Telegram command rejected: %s", error)
            self._send_message(update.chat_id, f"Error: {error}")
        except Exception:
            logger.exception("Unhandled Telegram update %s", update.update_id)
            self._send_message(
                update.chat_id,
                "Error: the bot hit an unexpected failure. Check logs.",
            )

    def _handle_command(
        self,
        chat_id: str,
        command: TelegramCommand,
        sender: str,
    ) -> None:
        if not self._is_chat_allowed(chat_id):
            return

        logger.info("Telegram command from %s in chat %s: /%s", sender, chat_id, command.name)

        if command.name == TelegramCommandName.UNKNOWN:
            self._handle_plain_text(chat_id, command.raw_text)
            return

        if command.name == TelegramCommandName.HELP:
            self._send_message(chat_id, self._help_text())
            return

        if command.name == TelegramCommandName.STATUS:
            self._send_message(chat_id, self._status_text(chat_id))
            return

        if command.name == TelegramCommandName.STOP:
            self._send_message(chat_id, "Stopping Telegram operator.")
            self._stop_runtime()
            return

        if command.name == TelegramCommandName.NEW:
            if command.argument:
                self._handle_backlog_proposal(chat_id, command.argument)
            else:
                self._handle_new_session(chat_id)
            return

        if command.name == TelegramCommandName.APPROVE:
            if self._handle_backlog_draft_decision(chat_id, approved=True):
                return
            self._handle_approval(chat_id, approved=True)
            return

        if command.name == TelegramCommandName.REJECT:
            if self._handle_backlog_draft_decision(chat_id, approved=False):
                return
            self._handle_approval(chat_id, approved=False)
            return

        if chat_id in self._active_tasks:
            self._send_message(
                chat_id,
                "A task is already active. Reply /approve or /reject before starting a new one.",
            )
            return

        if command.name == TelegramCommandName.RUN:
            self._run_backlog_task(chat_id, command.argument)
            return

        if command.name in {TelegramCommandName.CODE, TelegramCommandName.FIX}:
            self._run_code_task(chat_id, command.argument, command_name=command.name)
            return

        self._send_message(chat_id, self._help_text())

    def _run_backlog_task(self, chat_id: str, task_id: str) -> None:
        backlog_item = load_backlog_item_by_id(task_id)
        graph_state = backlog_item_to_graph_state(backlog_item)
        task_label = f"{backlog_item.item_id} - {backlog_item.title}"
        request_summary = _summarize_text(backlog_item.body or backlog_item.title)
        self._run_graph_task(
            chat_id=chat_id,
            task_label=task_label,
            request_summary=request_summary,
            graph_state=graph_state,
        )

    def _handle_plain_text(self, chat_id: str, text: str) -> None:
        if chat_id in self._active_tasks:
            self._send_message(
                chat_id,
                "A coding task is active. Reply /approve or /reject before normal chat.",
            )
            return
        if chat_id in self._pending_backlog_drafts:
            self._send_message(
                chat_id,
                "A backlog draft is waiting. Reply /approve or /reject before normal chat.",
            )
            return
        if not self._settings.orchestrator_ai_enabled:
            self._send_message(
                chat_id,
                "I can route normal text when orchestrator AI is enabled. "
                "For now use /code for a coding task, /run to implement a backlog item, or /help.",
            )
            return

        try:
            reply = run_telegram_agent_message(
                app=self._get_telegram_agent_app(),
                thread_id=self._telegram_agent_thread_id(chat_id),
                text=text,
            )
        except Exception:
            logger.exception("Telegram agent graph failed for chat %s", chat_id)
            self._send_message(chat_id, "Error: Telegram agent graph failed. Check logs.")
            return

        self._send_message(chat_id, reply.text)

    def _handle_backlog_proposal(self, chat_id: str, text: str) -> None:
        if chat_id in self._active_tasks:
            self._send_message(chat_id, "A coding task is active. Reply /approve or /reject before creating backlog work.")
            return
        if chat_id in self._pending_backlog_drafts:
            self._send_message(chat_id, "A backlog draft is already waiting. Reply /approve or /reject first.")
            return

        repository = MarkdownBacklogRepository(Path(self._settings.backlog_path))
        try:
            result = build_backlog_draft_from_text(
                text=text,
                repository=repository,
                settings=self._settings,
            )
        except BacklogDraftBuildError as error:
            self._send_message(chat_id, f"Backlog draft failed: {error}")
            return

        self._pending_backlog_drafts[chat_id] = PendingBacklogDraft(chat_id=chat_id, draft=result.draft)
        self._send_message(
            chat_id,
            _backlog_draft_prompt(result.draft, source=result.source, limit=self._settings.telegram_max_message_chars),
        )

    def _handle_backlog_draft_decision(self, chat_id: str, *, approved: bool) -> bool:
        pending_draft = self._pending_backlog_drafts.get(chat_id)
        if pending_draft is None:
            return False

        self._pending_backlog_drafts.pop(chat_id, None)
        if not approved:
            self._send_message(chat_id, f"Backlog draft rejected: {pending_draft.draft.item_id}")
            return True

        repository = MarkdownBacklogRepository(Path(self._settings.backlog_path))
        item = repository.add_item(pending_draft.draft)
        self._send_message(chat_id, f"Backlog item added: {item.item_id} - {item.title}")
        return True

    def _run_code_task(
        self,
        chat_id: str,
        text: str,
        *,
        command_name: TelegramCommandName,
    ) -> None:
        normalized_text = text.strip()
        command_label = f"/{command_name.value}"
        if not normalized_text:
            raise ValueError(f"{command_label} requires a short task description.")
        if len(normalized_text) > self._settings.telegram_max_fix_request_chars:
            raise ValueError(
                f"{command_label} text is too long. Keep it under {self._settings.telegram_max_fix_request_chars} characters."
            )

        request_summary = _summarize_text(normalized_text)
        task_label = f"Ad hoc code task - {request_summary}"
        graph_state = _base_graph_state(
            request=(
                "Telegram explicit coding request\n"
                f"Command: {command_label}\n"
                f"Title: {request_summary}\n\n"
                f"{normalized_text}"
            )
        )
        self._run_graph_task(
            chat_id=chat_id,
            task_label=task_label,
            request_summary=request_summary,
            graph_state=graph_state,
        )

    def _run_graph_task(
        self,
        *,
        chat_id: str,
        task_label: str,
        request_summary: str,
        graph_state: GraphState,
    ) -> None:
        if chat_id in self._active_tasks:
            self._send_message(
                chat_id,
                "A task is already active. Reply /approve or /reject before starting a new one.",
            )
            return

        self._send_message(chat_id, f"Working on: {request_summary}")
        memory = MemorySaver()
        app = build_graph(
            checkpointer_storage=memory,
            execute_coding_agent_override=self._execute_coding_agent_override,
        )
        thread_config: RunnableConfig = {
            "configurable": {"thread_id": f"telegram-{self._session_id}-{chat_id}-{uuid.uuid4().hex}"}
        }

        try:
            app.invoke(graph_state, config=thread_config)
            state_snapshot = app.get_state(thread_config)
        except Exception:
            logger.exception("Telegram graph task failed: %s", task_label)
            raise

        if state_snapshot.next:
            self._active_tasks[chat_id] = ActiveTelegramTask(
                chat_id=chat_id,
                task_label=task_label,
                request_summary=request_summary,
                app=app,
                thread_config=thread_config,
            )
            self._send_message(
                chat_id,
                _approval_prompt(
                    task_label=task_label,
                    request_summary=request_summary,
                    approval_reason=str(state_snapshot.values["approval_reason"]),
                    limit=self._settings.telegram_max_message_chars,
                ),
            )
            return

        self._send_message(
            chat_id,
            _completion_message(
                task_label,
                state_values=state_snapshot.values,
                limit=self._settings.telegram_max_message_chars,
            ),
        )

    def _handle_approval(self, chat_id: str, approved: bool) -> None:
        active_task = self._active_tasks.get(chat_id)
        if active_task is None:
            self._send_message(chat_id, "No task is waiting for approval.")
            return

        try:
            active_task.app.update_state(
                active_task.thread_config,
                {"approved": approved},
            )
            active_task.app.invoke(None, config=active_task.thread_config)
            state_snapshot = active_task.app.get_state(active_task.thread_config)
        except Exception:
            logger.exception(
                "Failed to resume Telegram task after approval decision: %s",
                active_task.task_label,
            )
            raise

        if approved:
            self._active_tasks.pop(chat_id, None)
            self._send_message(
                chat_id,
                _completion_message(
                    active_task.task_label,
                    state_values=state_snapshot.values,
                    limit=self._settings.telegram_max_message_chars,
                ),
            )
            return

        self._active_tasks.pop(chat_id, None)
        self._send_message(chat_id, f"Task rejected and closed: {active_task.task_label}")

    def _status_text(self, chat_id: str) -> str:
        execution_state = "ENABLED" if self._coding_agent_execution_enabled() else "DISABLED"
        telegram_state = "ENABLED" if self._settings.telegram_enabled else "DISABLED"
        transport_state = self._settings.telegram_transport.upper()
        active_task = self._active_tasks.get(chat_id)
        active_chat_count = len(self._active_tasks)
        pending_backlog_count = len(self._pending_backlog_drafts)

        lines = [
            (
                "Bot is alive. "
                f"Telegram: {telegram_state}. "
                f"Transport: {transport_state}. "
                f"Coding-agent execution: {execution_state}."
            ),
            f"Orchestrator AI model: {self._settings.orchestrator_ai_model}.",
            f"Active coding tasks: {active_chat_count}.",
            f"Pending backlog drafts: {pending_backlog_count}.",
        ]
        if active_task is None:
            lines.append("This chat: no active task.")
        else:
            lines.append(f"This chat: {active_task.task_label}")
            lines.append("Reply /approve or /reject.")

        return "\n".join(lines)

    def _handle_new_session(self, chat_id: str) -> None:
        active_task = self._active_tasks.pop(chat_id, None)
        self._pending_backlog_drafts.pop(chat_id, None)
        self._reset_telegram_agent_memory()
        self._session_id = uuid.uuid4().hex

        if active_task is None:
            self._send_message(chat_id, self._fresh_session_text())
            return

        self._send_message(
            chat_id,
            "\n".join(
                [
                    "Fresh session ready.",
                    f"Discarded paused task: {active_task.task_label}.",
                    *self._session_intro_lines(),
                ]
            ),
        )

    def _help_text(self) -> str:
        identity = _load_orchestrator_identity_summary()
        execution_state = "enabled" if self._coding_agent_execution_enabled() else "disabled"
        return "\n".join(
            [
                identity,
                f"Orchestrator AI model: {self._settings.orchestrator_ai_model} ({execution_state}).",
                "Commands:",
                "/help - show this help",
                "/new - start a fresh session",
                "/new <text> - propose a backlog item",
                "/run ATL-001 - implement a backlog item by ID",
                "/code <text> - explicit coding workflow",
                "/fix <text> - old alias for /code",
                "/status - check bot status",
                "/approve - approve the paused task",
                "/reject - reject the paused task",
                "/stop - stop the polling loop",
            ]
        )

    def _fresh_session_text(self) -> str:
        return "\n".join(
            [
                "Fresh session ready.",
                f"Model: {self._settings.orchestrator_ai_model}.",
                "Use /help for commands.",
            ]
        )

    def _session_intro_lines(self) -> list[str]:
        identity = _load_orchestrator_identity_summary()
        execution_state = "enabled" if self._coding_agent_execution_enabled() else "disabled"
        return [
            identity,
            f"Orchestrator AI model: {self._settings.orchestrator_ai_model} ({execution_state}).",
            "Use normal text to ask the orchestrator, /new <text> for backlog work, /code for explicit coding work, or /run to implement an existing backlog item.",
        ]

    def _get_telegram_agent_app(self) -> Any:
        if self._telegram_agent_app is None:
            self._telegram_agent_app = build_telegram_agent_graph(
                settings=self._settings,
                checkpointer=self._telegram_agent_memory,
            )
        return self._telegram_agent_app

    def _telegram_agent_thread_id(self, chat_id: str) -> str:
        return f"telegram-agent-{self._session_id}-{chat_id}"

    def _reset_telegram_agent_memory(self) -> None:
        self._telegram_agent_memory = MemorySaver()
        self._telegram_agent_app = None

    def _send_message(self, chat_id: str, text: str) -> None:
        self._client.send_message(
            chat_id,
            _truncate_text(text, self._settings.telegram_max_message_chars),
        )

    def _is_chat_allowed(self, chat_id: str) -> bool:
        if not self._allowed_chat_ids:
            return True

        if chat_id in self._allowed_chat_ids:
            return True

        self._send_message(
            chat_id,
            "This chat is not allowed to use the bot.",
        )
        return False

    def _stop_runtime(self) -> None:
        self._running = False
        if self._shutdown_callback is not None:
            self._shutdown_callback()

    def _set_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self._shutdown_callback = callback

    def _coding_agent_execution_enabled(self) -> bool:
        if self._execute_coding_agent_override is not None:
            return self._execute_coding_agent_override
        return self._settings.execute_coding_agent

    def _build_webhook_handler(
        self,
        *,
        expected_path: str,
        secret_token: str,
    ) -> type[BaseHTTPRequestHandler]:
        operator = self

        class TelegramWebhookHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != expected_path:
                    self.send_error(404, "Not found")
                    return

                if secret_token:
                    request_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
                    if request_secret != secret_token:
                        self.send_error(401, "Unauthorized")
                        return

                try:
                    update_data = self._read_json_body()
                    operator.handle_update_data(update_data)
                except ValueError as error:
                    logger.info("Telegram webhook request rejected: %s", error)
                    self._send_json({"error": str(error)}, status=400)
                    return
                except Exception:
                    logger.exception("Unhandled Telegram webhook request")
                    self._send_json(
                        {"error": "The bot hit an unexpected failure."},
                        status=500,
                    )
                    return

                self._send_json({"ok": True})

            def log_message(self, format: str, *args: object) -> None:
                return

            def _read_json_body(self) -> Any:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length).decode("utf-8")
                try:
                    return json.loads(body)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Webhook body must be valid JSON: {error}") from error

            def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
                encoded_body = (json.dumps(data, indent=2) + "\n").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded_body)))
                self.end_headers()
                self.wfile.write(encoded_body)

        return TelegramWebhookHandler

    def handle_update_data(self, update_data: Any) -> None:
        update = parse_telegram_update(update_data)
        if update is None:
            raise ValueError("Telegram update did not contain a message.")
        self._handle_update(update)


def parse_telegram_command(text: str) -> TelegramCommand:
    """Parse a Telegram slash command from the incoming message text."""

    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("Telegram message cannot be empty.")
    if not normalized_text.startswith("/"):
        return TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text=normalized_text)

    first_line = normalized_text.splitlines()[0]
    command_text = first_line.split(maxsplit=1)[0]
    command_name = command_text[1:].split("@", 1)[0].lower()
    if command_name == "start":
        command_name = TelegramCommandName.HELP
    if command_name not in TelegramCommandName._value2member_map_:
        return TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text=normalized_text)

    argument = normalized_text[len(command_text):].lstrip()

    if command_name in {
        TelegramCommandName.HELP,
        TelegramCommandName.STATUS,
        TelegramCommandName.STOP,
        TelegramCommandName.APPROVE,
        TelegramCommandName.REJECT,
    }:
        if argument:
            raise ValueError(f"/{command_name} does not accept extra text.")
        return TelegramCommand(
            name=TelegramCommandName(command_name),
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.NEW:
        return TelegramCommand(
            name=TelegramCommandName.NEW,
            argument=argument.strip(),
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.RUN:
        task_id = argument.splitlines()[0].strip().upper() if argument else ""
        if not re.fullmatch(r"[A-Z]+-\d{3}", task_id):
            raise ValueError("/run requires a backlog item ID like ATL-001.")
        return TelegramCommand(
            name=TelegramCommandName.RUN,
            argument=task_id,
            raw_text=normalized_text,
        )

    if command_name in {TelegramCommandName.CODE, TelegramCommandName.FIX}:
        if not argument.strip():
            raise ValueError(f"/{command_name} requires a short task description.")
        return TelegramCommand(
            name=TelegramCommandName(command_name),
            argument=argument,
            raw_text=normalized_text,
        )

    return TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text=normalized_text)


def parse_telegram_update(update_data: Any) -> TelegramUpdate | None:
    """Parse the Telegram update payload into the local typed shape."""

    if not isinstance(update_data, dict):
        return None

    update_id = update_data.get("update_id")
    message = update_data.get("message")
    if not isinstance(update_id, int) or not isinstance(message, dict):
        return None

    text = message.get("text")
    chat = message.get("chat")
    if not isinstance(text, str) or not isinstance(chat, dict):
        return None

    chat_id = chat.get("id")
    if chat_id is None:
        return None

    sender = _extract_sender(message)
    return TelegramUpdate(
        update_id=update_id,
        chat_id=str(chat_id),
        text=text,
        sender=sender,
    )


def get_telegram_bot_token() -> str:
    """Read the Telegram bot token from the local secret file."""

    token = load_telegram_secrets().bot_token.strip()
    if not token:
        raise RuntimeError(
            "Telegram bot token is required in the local secret file under data/."
        )
    return token


def run_telegram_operator(*, execute_coding_agent_override: bool | None = None) -> None:
    """Start the configured Telegram transport using the current settings."""

    settings = load_settings()
    if not settings.telegram_enabled:
        logger.info("Telegram operator disabled by settings.")
        return

    operator = TelegramOperator(
        get_telegram_bot_token(),
        settings,
        execute_coding_agent_override=execute_coding_agent_override,
    )
    operator.run()


def _base_graph_state(request: str) -> GraphState:
    return {
        "request": request,
        "brief": "",
        "needs_approval": False,
        "approval_reason": "Risk review has not run yet.",
        "approved": False,
        "agent_instruction": "",
        "coding_agent_result": "",
    }


def _approval_prompt(
    task_label: str,
    request_summary: str,
    approval_reason: str,
    *,
    limit: int,
) -> str:
    reason = _telegram_approval_reason(approval_reason)
    return _truncate_text(
        "\n".join(
            [
                "Approval needed",
                f"Task: {request_summary}",
                f"Why: {reason}",
                "Reply /approve or /reject.",
            ]
        ),
        limit,
    )


def _telegram_approval_reason(approval_reason: str) -> str:
    normalized_reason = " ".join(approval_reason.split())
    if "not configured" in normalized_reason.lower() or "ai risk review is off" in normalized_reason.lower():
        return "AI risk review is off, so I need your approval before continuing."
    if "failed" in normalized_reason.lower() or "invalid risk review" in normalized_reason.lower():
        return "AI risk review failed, so I need your approval before continuing."
    if "low confidence" in normalized_reason.lower():
        return "AI was not confident enough, so I need your approval before continuing."
    if normalized_reason.startswith("Orchestrator AI risk review:"):
        normalized_reason = normalized_reason.removeprefix("Orchestrator AI risk review:").strip()
    return _summarize_text(normalized_reason, limit=180)


def _backlog_draft_prompt(draft: BacklogDraft, *, source: str, limit: int) -> str:
    return _truncate_text(
        "\n".join(
            [
                "Backlog draft ready",
                f"ID: {draft.item_id}",
                f"Title: {draft.title}",
                f"Source: {source}",
                "",
                render_backlog_draft(draft),
                "",
                "Reply /approve to add it or /reject to discard it.",
            ]
        ),
        limit,
    )


def _completion_message(
    task_label: str,
    state_values: dict[str, Any],
    *,
    limit: int,
) -> str:
    coding_agent_result = str(state_values.get("coding_agent_result", "")).strip()
    if not coding_agent_result:
        return _truncate_text(f"Task complete: {task_label}", limit)

    # Completion summary rules live in docs/prompts/completion_summary_prompt.md.
    # summary() format: friendly message, duration, changed files, token notice, then
    # Command:/Return code:/Timed out:/Stdout:/Stderr: (verbose, not shown on Telegram).
    _VERBOSE_PREFIXES = ("Command:", "Return code:", "Timed out:", "Stdout:", "Stderr:")
    summary_lines: list[str] = []
    for line in coding_agent_result.splitlines():
        if line.startswith(_VERBOSE_PREFIXES):
            break
        summary_lines.append(line)

    body = "\n".join(summary_lines).strip() or coding_agent_result.splitlines()[0]
    return _truncate_text(f"Task complete: {task_label}\n{body}", limit)


def _load_orchestrator_identity_summary() -> str:
    if not ORCHESTRATOR_IDENTITY_PATH.exists():
        raise FileNotFoundError(f"Orchestrator identity file not found: {ORCHESTRATOR_IDENTITY_PATH}")

    content = ORCHESTRATOR_IDENTITY_PATH.read_text(encoding="utf-8")
    name = _extract_markdown_section(content, "Name")
    purpose = _extract_markdown_section(content, "Purpose")
    role = _extract_markdown_section(content, "Operating Role")
    return "\n".join(
        [
            name,
            f"Purpose: {purpose}",
            f"Role: {role}",
        ]
    )


def _extract_markdown_section(content: str, heading: str) -> str:
    marker = f"## {heading}"
    start = content.find(marker)
    if start == -1:
        raise ValueError(f"Orchestrator identity is missing section: {heading}")
    next_start = content.find("\n## ", start + len(marker))
    section = content[start:next_start].strip() if next_start != -1 else content[start:].strip()
    lines = [line.strip() for line in section.splitlines()]
    body_lines = [line for line in lines[1:] if line and not line.startswith("## ")]
    return " ".join(body_lines).strip()


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _summarize_text(text: str, limit: int = 120) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
    return _truncate_text(first_line, limit)


def _extract_sender(message: dict[str, Any]) -> str:
    user = message.get("from")
    if isinstance(user, dict):
        username = user.get("username")
        if isinstance(username, str) and username.strip():
            return username.strip()
        first_name = user.get("first_name")
        if isinstance(first_name, str) and first_name.strip():
            return first_name.strip()
    return "unknown"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"
