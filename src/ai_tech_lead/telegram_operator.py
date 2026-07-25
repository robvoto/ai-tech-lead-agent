"""Telegram adapter for the local AI Tech Lead workflow.

This module keeps the Telegram surface deliberately small:
- polling and webhook transports
- one active task per chat
- explicit command handling
- no hidden task selection
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from .app_settings import AppSettings, load_settings, save_settings
from .backlog_draft_builder import (
    BacklogRefinementBuildError,
    build_backlog_refinement_from_text,
)
from .backlog_loader import backlog_item_to_graph_state
from .backlog_repository import (
    BacklogItem,
    BacklogRefinementDraft,
    BacklogValidationError,
    format_backlog_list_item,
)
from .backlog_runtime_store import (
    BacklogRuntimeStore,
    enqueue_and_flush_update,
    run_pending_backlog_recovery,
)
from .backlog_sheets_repository import (
    BacklogSourceUnavailableError,
    repository_from_settings,
)
from .backlog_status import BacklogStatus, backlog_status_choices, normalize_backlog_status
from .checkpointer_store import get_checkpointer
from .coding_agent_runner import CodingAgentCancellationToken
from .coding_workflow_graph import GraphState, build_graph, build_initial_graph_state
from .config import PROJECT_ROOT
from .logging_setup import LOGGER_NAME
from .telegram_agent_graph import (
    TelegramAgentReply,
    build_telegram_agent_graph,
    run_telegram_agent_message,
)
from .telegram_secrets import get_telegram_bot_token

logger = logging.getLogger(LOGGER_NAME)
ORCHESTRATOR_IDENTITY_PATH = PROJECT_ROOT / "docs" / "ORCHESTRATOR_IDENTITY.md"
LOG_SEPARATOR = "----------------------------------------"


@dataclass(frozen=True)
class _BotCommand:
    name: str
    usage: str
    description: str

    def help_line(self) -> str:
        if self.usage:
            return f"/{self.name} {self.usage} - {self.description}"
        return f"/{self.name} - {self.description}"

    def to_api_dict(self) -> dict[str, str]:
        return {"command": self.name, "description": self.description}


CANONICAL_BOT_COMMAND_SECTIONS: tuple[tuple[str, tuple[_BotCommand, ...]], ...] = (
    (
        "General",
        (
            _BotCommand("help", "", "show command list"),
            _BotCommand("commands", "", "alias for /help"),
            _BotCommand("status", "", "check bot status"),
        ),
    ),
    (
        "Task control",
        (
            _BotCommand("new", "", "start a fresh session and cancel current active task"),
            _BotCommand("code", "<text>", "explicit coding workflow"),
            _BotCommand("cancel_code", "", "stop the running coding-agent subprocess"),
            _BotCommand("approve", "", "approve the waiting task/decision"),
            _BotCommand(
                "request_changes",
                "<feedback>",
                "send feedback on a waiting approval and get a revised proposal",
            ),
            _BotCommand("ask", "<question>", "ask a question about a waiting approval"),
            _BotCommand("cancel", "", "cancel the waiting task/decision"),
            _BotCommand("reject", "", "alias for /cancel"),
            _BotCommand("sleep", "on|off", "sleep mode: auto-run LOW/MEDIUM risk tasks"),
        ),
    ),
    (
        "Backlog",
        (
            _BotCommand("propose", "<text>", "create/refine a backlog draft from an idea"),
            _BotCommand("run", "<backlog-id>", "run backlog item, e.g. /run ATL-001"),
            _BotCommand(
                "next",
                "[limit]",
                "show top backlog items by priority (default 3)",
            ),
            _BotCommand("list", "[limit|all]", "list backlog items sorted by priority"),
            _BotCommand("count", "", "count backlog items"),
            _BotCommand("read", "<backlog-id>", "read backlog item details"),
            _BotCommand(
                "set_status",
                "<backlog-id> <status|1-8>",
                "propose backlog status update "
                "(1=Backlog 2=Not Done 3=In Progress 4=Needs Review 5=Blocked "
                "6=Done 7=Won't Do 8=Obsolete)",
            ),
        ),
    ),
)


CANONICAL_BOT_COMMANDS: tuple[_BotCommand, ...] = tuple(
    command
    for _, section_commands in CANONICAL_BOT_COMMAND_SECTIONS
    for command in section_commands
)


class TelegramCommandName(StrEnum):
    """Supported Telegram command names."""

    HELP = "help"
    NEW = "new"
    PROPOSE = "propose"
    RUN = "run"
    NEXT = "next"
    CODE = "code"
    STATUS = "status"
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"
    ASK = "ask"
    CANCEL = "cancel"
    CANCEL_CODE = "cancel_code"
    SLEEP = "sleep"
    LIST = "list"
    COUNT = "count"
    READ = "read"
    SET_STATUS = "set_status"
    UNKNOWN = "unknown"


class TelegramTaskStage(StrEnum):
    """Which orchestrator input gate the chat is currently waiting on."""

    PRE_RUN_APPROVAL = "pre_run_approval"
    RESEARCH_APPROVAL = "research_approval"
    ORCHESTRATOR_INPUT = "orchestrator_input"
    COMPLETION_VERIFICATION = "completion_verification"
    RUNNING = "running"


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
    """Backlog refinement draft waiting for explicit Telegram approval."""

    chat_id: str
    draft: BacklogRefinementDraft


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
    stage: TelegramTaskStage
    backlog_item_id: str | None = None
    cancellation_token: CodingAgentCancellationToken | None = None


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

    def delete_my_commands(self) -> None:
        """Remove all commands from the bot's Telegram command menu."""
        self._post("deleteMyCommands", {})

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        """Register the bot command menu with Telegram."""
        self._post("setMyCommands", {"commands": json.dumps(commands)})

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
        request_timeout_seconds = self._poll_timeout_seconds + 10
        payload_keys = ",".join(sorted(payload.keys()))

        try:
            with self._opener(request, timeout=request_timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            logger.exception(
                "Telegram API HTTP error method=%s base_url=%s timeout=%ss payload_keys=%s",
                method,
                self._base_url,
                request_timeout_seconds,
                payload_keys,
            )
            raise RuntimeError(f"Telegram API error calling {method}: {error_body}") from error
        except URLError as error:
            logger.exception(
                "Telegram API request failed method=%s base_url=%s timeout=%ss payload_keys=%s",
                method,
                self._base_url,
                request_timeout_seconds,
                payload_keys,
            )
            raise RuntimeError(
                f"Telegram API request failed for {method}: {error.reason}"
            ) from error

        data = json.loads(body)
        if not isinstance(data, dict):
            raise RuntimeError(f"Telegram API returned invalid JSON for {method}.")
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram API call failed for {method}: {data}")
        return data


def _interrupt_from_snapshot(state_snapshot: Any) -> Any:
    """Return the first interrupt value from a paused LangGraph state snapshot, or None."""
    tasks = getattr(state_snapshot, "tasks", None) or []
    if tasks and getattr(tasks[0], "interrupts", None):
        return tasks[0].interrupts[0].value
    return None


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
        self._telegram_agent_app: Any | None = None
        self._telegram_agent_session_cost_usd = 0.0
        self._session_id = uuid.uuid4().hex
        logger.warning(
            "TelegramOperator initialised (session=%s). "
            "Active tasks reset to empty — any in-flight task from a previous process run "
            "has been lost. If a coding agent was running, its result is unknown.",
            self._session_id,
        )
        self._allowed_chat_ids = {
            chat_id.strip() for chat_id in settings.telegram_allowed_chat_ids if chat_id.strip()
        }
        self._chat_processing_locks: dict[str, threading.Lock] = {}
        self._chat_locks_registry = threading.Lock()

    def run(self) -> None:
        """Run the configured Telegram transport until process shutdown."""

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

        self._register_commands()

        while self._running:
            try:
                updates = self._client.get_updates(
                    offset=offset,
                    timeout_seconds=self._settings.telegram_long_poll_timeout_seconds,
                )
            except (TimeoutError, OSError):
                logger.debug("Telegram poll timed out or network error, retrying")
                continue
            for update in updates:
                offset = update.update_id + 1
                threading.Thread(
                    target=self._handle_update,
                    args=(update,),
                    daemon=True,
                ).start()

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
            self._register_commands()
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

    # Commands that must run immediately even if another message is being processed.
    _PRIORITY_COMMANDS: frozenset[TelegramCommandName] = frozenset(
        {TelegramCommandName.NEW, TelegramCommandName.CANCEL_CODE}
    )

    def _get_chat_lock(self, chat_id: str) -> threading.Lock:
        with self._chat_locks_registry:
            if chat_id not in self._chat_processing_locks:
                self._chat_processing_locks[chat_id] = threading.Lock()
            return self._chat_processing_locks[chat_id]

    def _handle_update(self, update: TelegramUpdate) -> None:
        command = parse_telegram_command(update.text)
        if command.name in self._PRIORITY_COMMANDS:
            self._dispatch_command(update.chat_id, command, update.sender)
        else:
            with self._get_chat_lock(update.chat_id):
                self._dispatch_command(update.chat_id, command, update.sender)

    def _dispatch_command(self, chat_id: str, command: TelegramCommand, sender: str) -> None:
        try:
            self._handle_command(chat_id, command, sender)
        except ValueError as error:
            logger.info("Telegram command rejected: %s", error)
            self._send_message(chat_id, f"Error: {error}")
        except Exception:
            logger.exception("Unhandled Telegram command in chat %s", chat_id)
            self._send_message(
                chat_id,
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

        logger.info(LOG_SEPARATOR)
        logger.info("Telegram command from %s in chat %s: /%s", sender, chat_id, command.name)

        if command.name == TelegramCommandName.UNKNOWN:
            logger.info(
                "Telegram action: plain text received in chat %s from %s.",
                chat_id,
                sender,
            )
            self._handle_plain_text(chat_id, command.raw_text, sender)
            return

        if command.name == TelegramCommandName.HELP:
            logger.info("Telegram action: showing help for chat %s.", chat_id)
            self._send_message(chat_id, self._help_text())
            return

        if command.name == TelegramCommandName.STATUS:
            logger.info("Telegram action: showing status for chat %s.", chat_id)
            self._send_message(chat_id, self._status_text(chat_id))
            return

        if command.name == TelegramCommandName.PROPOSE:
            logger.info(
                "Telegram action: refining backlog idea in chat %s (%s characters).",
                chat_id,
                len(command.argument),
            )
            self._handle_backlog_proposal(chat_id, command.argument)
            return

        if command.name == TelegramCommandName.NEW:
            logger.info(
                "Telegram action: resetting session in chat %s for project root %s "
                "(hardcoded default for now).",
                chat_id,
                self._settings.project_root,
            )
            self._handle_new_session(chat_id)
            return

        if command.name == TelegramCommandName.APPROVE:
            logger.info("Telegram action: approve requested in chat %s.", chat_id)
            if self._handle_backlog_draft_decision(chat_id, approved=True):
                return
            if self._handle_pre_run_approval(chat_id, sender=sender, action="approve"):
                return
            self._send_message(
                chat_id,
                "No approval is waiting. If you have a clarification, send normal "
                "text while orchestrator input is pending.",
            )
            return

        if command.name in {TelegramCommandName.REJECT, TelegramCommandName.CANCEL}:
            logger.info("Telegram action: cancel requested in chat %s.", chat_id)
            if self._handle_backlog_draft_decision(chat_id, approved=False):
                return
            if self._handle_pre_run_approval(
                chat_id, sender=sender, action="cancel", text=command.argument
            ):
                return
            self._send_message(
                chat_id,
                "No active task is waiting, so there is nothing to cancel.",
            )
            return

        if command.name == TelegramCommandName.REQUEST_CHANGES:
            logger.info("Telegram action: request_changes requested in chat %s.", chat_id)
            if self._handle_pre_run_approval(
                chat_id, sender=sender, action="request_changes", text=command.argument
            ):
                return
            self._send_message(
                chat_id,
                "No approval is waiting, so there is nothing to request changes on.",
            )
            return

        if command.name == TelegramCommandName.ASK:
            logger.info("Telegram action: ask requested in chat %s.", chat_id)
            if self._handle_pre_run_approval(
                chat_id, sender=sender, action="ask_question", text=command.argument
            ):
                return
            self._send_message(
                chat_id,
                "No approval is waiting, so there is no question to ask about.",
            )
            return

        if command.name == TelegramCommandName.CANCEL_CODE:
            logger.info("Telegram action: cancel_code requested in chat %s.", chat_id)
            self._handle_cancel_code(chat_id)
            return

        if command.name == TelegramCommandName.SLEEP:
            logger.info("Telegram action: /sleep %s in chat %s.", command.argument, chat_id)
            self._handle_sleep(chat_id, command.argument)
            return

        if command.name == TelegramCommandName.LIST:
            logger.info("Telegram action: /list in chat %s.", chat_id)
            self._handle_list(chat_id, command.argument)
            return

        if command.name == TelegramCommandName.NEXT:
            logger.info("Telegram action: /next in chat %s.", chat_id)
            self._handle_next(chat_id, command.argument)
            return

        if command.name == TelegramCommandName.COUNT:
            logger.info("Telegram action: /count in chat %s.", chat_id)
            self._handle_count(chat_id)
            return

        if command.name == TelegramCommandName.READ:
            logger.info("Telegram action: /read %s in chat %s.", command.argument, chat_id)
            self._handle_read(chat_id, command.argument)
            return

        if command.name == TelegramCommandName.SET_STATUS:
            logger.info("Telegram action: /set_status %s in chat %s.", command.argument, chat_id)
            self._handle_set_status(chat_id, command.argument)
            return

        if chat_id in self._active_tasks:
            active_task = self._active_tasks[chat_id]
            logger.info(
                "Telegram action: chat %s is waiting on %s; sending reminder.",
                chat_id,
                active_task.stage,
            )
            reminder = self._orchestrator_input_expectation_message(
                active_task=active_task,
                state_values=self._active_task_state_values(active_task),
            )
            self._send_message(
                chat_id,
                reminder,
            )
            return

        if command.name == TelegramCommandName.RUN:
            logger.info(
                "Telegram action: running backlog item %s in chat %s.", command.argument, chat_id
            )
            self._run_backlog_task(chat_id, command.argument)
            return

        if command.name == TelegramCommandName.CODE:
            logger.info("Telegram action: starting ad hoc code task in chat %s.", chat_id)
            self._run_code_task(chat_id, command.argument, command_name=command.name)
            return

        self._send_message(chat_id, self._help_text())

    def _run_backlog_task(self, chat_id: str, task_id: str) -> None:
        repository = self._get_repository()
        try:
            source_record = repository.get_item_with_source(task_id)
        except (ValueError, BacklogValidationError, BacklogSourceUnavailableError) as error:
            logger.info(
                "Telegram run: backlog item selection failed for chat %s: %s", chat_id, error
            )
            self._send_message(chat_id, str(error))
            return

        backlog_item = source_record.item
        if backlog_item.status in {
            BacklogStatus.DONE,
            BacklogStatus.WONT_DO,
            BacklogStatus.OBSOLETE,
            BacklogStatus.DEFERRED,
        }:
            message = (
                f"Backlog item '{backlog_item.item_id}' is Done and cannot be selected "
                "for execution."
            )
            logger.info("Telegram run: %s", message)
            self._send_message(chat_id, message)
            return

        reference = repository.reference
        BacklogRuntimeStore().save_snapshot(
            request_id=_telegram_backlog_request_id(backlog_item.item_id),
            project_key=reference.project_key,
            spreadsheet_id=reference.spreadsheet_id,
            sheet_name=reference.sheet_name,
            item_id=backlog_item.item_id,
            row_data=source_record.row_values,
            row_hash=source_record.row_hash,
            fetched_at=source_record.fetched_at,
        )

        graph_state = backlog_item_to_graph_state(backlog_item)
        task_label = f"{backlog_item.item_id} - {backlog_item.title}"
        request_summary = _backlog_request_summary(backlog_item)
        logger.info(LOG_SEPARATOR)
        logger.info(
            "Telegram run: backlog item resolved for chat %s: %s - %s",
            chat_id,
            backlog_item.item_id,
            backlog_item.title,
        )
        logger.info("Telegram run: backlog request summary: %s", request_summary)
        logger.info("Telegram run: loading graph task from backlog item %s.", backlog_item.item_id)
        self._send_message(
            chat_id,
            _backlog_start_message(backlog_item, limit=self._settings.telegram_max_message_chars),
        )
        self._run_graph_task(
            chat_id=chat_id,
            task_label=task_label,
            request_summary=request_summary,
            graph_state=graph_state,
            backlog_item_id=backlog_item.item_id,
        )

    def _handle_plain_text(self, chat_id: str, text: str, sender: str = "") -> None:
        if chat_id in self._active_tasks:
            active_task = self._active_tasks[chat_id]

            if active_task.stage == TelegramTaskStage.ORCHESTRATOR_INPUT:
                # All plain text is treated as clarification/guidance input.
                # The conversational agent answers first (if enabled), then the graph resumes.
                # The clarification checker decides whether enough info has been gathered.
                if self._settings.orchestrator_ai_enabled:
                    try:
                        context_text = (
                            f"[ACTIVE TASK: {active_task.task_label}]\n"
                            f"[WAITING FOR: clarification]\n\n"
                            f"{text}"
                        )
                        reply = run_telegram_agent_message(
                            app=self._get_telegram_agent_app(),
                            thread_id=self._telegram_agent_thread_id(chat_id),
                            text=context_text,
                            session_cost_total_usd=self._telegram_agent_session_cost_usd,
                        )
                        self._record_telegram_agent_usage(reply)
                        self._send_message(chat_id, reply.text)
                    except Exception:
                        logger.exception("Telegram agent graph failed for chat %s", chat_id)
                self._resume_from_clarification(chat_id, active_task, text)
                return

            # For approval/research stages, route to the conversational agent.
            # The graph only resumes via a slash command (/approve, /request_changes,
            # /ask, /cancel for pre-run approval; /approve or /cancel for research).
            if not self._settings.orchestrator_ai_enabled:
                self._send_message(
                    chat_id,
                    f"Task is paused: {active_task.task_label}\n"
                    "Use /approve, /request_changes, /ask, or /cancel to continue.",
                )
                return

            try:
                reply = run_telegram_agent_message(
                    app=self._get_telegram_agent_app(),
                    thread_id=self._telegram_agent_thread_id(chat_id),
                    text=text,
                    session_cost_total_usd=self._telegram_agent_session_cost_usd,
                )
                self._record_telegram_agent_usage(reply)
            except Exception:
                logger.exception("Telegram agent graph failed for chat %s", chat_id)
                self._send_message(chat_id, "Error: Telegram agent graph failed. Check logs.")
                return

            self._send_message(chat_id, reply.text)
            return
        if chat_id in self._pending_backlog_drafts:
            self._send_message(
                chat_id,
                "A backlog refinement is waiting. Reply /approve or /reject before normal chat.",
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
                session_cost_total_usd=self._telegram_agent_session_cost_usd,
            )
            self._record_telegram_agent_usage(reply)
        except Exception:
            logger.exception("Telegram agent graph failed for chat %s", chat_id)
            self._send_message(chat_id, "Error: Telegram agent graph failed. Check logs.")
            return

        self._send_message(chat_id, reply.text)

    def _handle_backlog_proposal(self, chat_id: str, text: str) -> None:
        if chat_id in self._active_tasks:
            self._send_message(
                chat_id,
                "A coding task is active. Reply /approve or /reject before creating backlog work.",
            )
            return
        if chat_id in self._pending_backlog_drafts:
            self._send_message(
                chat_id, "A backlog refinement is already waiting. Reply /approve or /reject first."
            )
            return

        logger.info("Telegram action: building backlog refinement draft for chat %s.", chat_id)
        repository = self._get_repository()
        try:
            result = build_backlog_refinement_from_text(
                text=text,
                repository=repository,
                settings=self._settings,
            )
        except BacklogRefinementBuildError as error:
            self._send_message(chat_id, f"Backlog refinement failed: {error}")
            return

        self._pending_backlog_drafts[chat_id] = PendingBacklogDraft(
            chat_id=chat_id, draft=result.draft
        )
        self._send_message(
            chat_id,
            _backlog_refinement_prompt(
                result.draft,
                source=result.source,
                limit=self._settings.telegram_max_message_chars,
            ),
        )

    def _handle_backlog_draft_decision(self, chat_id: str, *, approved: bool) -> bool:
        pending_draft = self._pending_backlog_drafts.get(chat_id)
        if pending_draft is None:
            return False

        self._pending_backlog_drafts.pop(chat_id, None)
        if not approved:
            self._send_message(
                chat_id, f"Backlog refinement rejected: {pending_draft.draft.item_id}"
            )
            return True

        repository = self._get_repository()
        item = repository.add_refined_item(pending_draft.draft)
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
        if len(normalized_text) > self._settings.telegram_max_code_request_chars:
            raise ValueError(
                f"{command_label} text is too long. Keep it under "
                f"{self._settings.telegram_max_code_request_chars} characters."
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
        backlog_item_id: str | None = None,
    ) -> None:
        if chat_id in self._active_tasks:
            self._send_message(
                chat_id,
                "A task is already active. Reply /approve or /reject before starting a new one.",
            )
            return

        logger.info(LOG_SEPARATOR)
        logger.info("Telegram run: preparing graph task for chat %s.", chat_id)
        logger.info("Telegram run: task label: %s", task_label)
        logger.info("Telegram run: request summary: %s", request_summary)
        if backlog_item_id is not None:
            logger.info("Telegram run: backlog item id: %s", backlog_item_id)
        logger.info(
            "Telegram run: coding-agent execution override: %s",
            self._execute_coding_agent_override,
        )
        self._send_message(chat_id, f"Working on: {request_summary}")
        cancellation_token = CodingAgentCancellationToken()
        app = build_graph(
            checkpointer_storage=get_checkpointer(),
            execute_coding_agent_override=self._execute_coding_agent_override,
            coding_agent_progress_callback=self._coding_agent_progress_callback(
                chat_id=chat_id,
                request_summary=request_summary,
            ),
            coding_agent_cancellation_token=cancellation_token,
        )
        logger.info("Telegram run: graph compiled and checkpointer attached.")
        thread_config: RunnableConfig = {
            "configurable": {
                "thread_id": f"telegram-{self._session_id}-{chat_id}-{uuid.uuid4().hex}"
            }
        }
        logger.info("Telegram run: thread id: %s", thread_config["configurable"]["thread_id"])
        self._active_tasks[chat_id] = ActiveTelegramTask(
            chat_id=chat_id,
            task_label=task_label,
            request_summary=request_summary,
            app=app,
            thread_config=thread_config,
            stage=TelegramTaskStage.RUNNING,
            backlog_item_id=backlog_item_id,
            cancellation_token=cancellation_token,
        )

        logger.info("Telegram run: starting background graph worker for chat %s.", chat_id)
        worker = threading.Thread(
            target=self._run_graph_task_background,
            args=(chat_id, task_label, request_summary, graph_state, backlog_item_id),
            daemon=True,
        )
        worker.start()
        logger.info("Telegram run: background graph worker started for chat %s.", chat_id)

    def _run_graph_task_background(
        self,
        chat_id: str,
        task_label: str,
        request_summary: str,
        graph_state: GraphState,
        backlog_item_id: str | None,
    ) -> None:
        active_task = self._active_tasks.get(chat_id)
        if active_task is None:
            logger.info(
                "Telegram run: background worker found no active task for chat %s.", chat_id
            )
            return

        logger.info(LOG_SEPARATOR)
        logger.info("Telegram run: background graph invocation started for chat %s.", chat_id)
        try:
            active_task.app.invoke(graph_state, config=active_task.thread_config)
            state_snapshot = active_task.app.get_state(active_task.thread_config)
            logger.info("Telegram run: background graph invocation finished for chat %s.", chat_id)
        except Exception:
            logger.exception("Telegram graph task failed: %s", task_label)
            self._active_tasks.pop(chat_id, None)
            self._send_message(chat_id, "Error: coding workflow failed. Check logs.")
            return

        current_task = self._active_tasks.get(chat_id)
        if current_task is None or current_task.thread_config != active_task.thread_config:
            logger.info(
                "Telegram run: stale background result ignored for chat %s "
                "because the active task changed.",
                chat_id,
            )
            return

        if state_snapshot.next:
            logger.info(
                "Telegram run: graph paused for chat %s before %s.",
                chat_id,
                ", ".join(state_snapshot.next),
            )
            task_stage, pause_message = self._stage_and_message_from_snapshot(
                task_label=task_label,
                request_summary=request_summary,
                state_snapshot=state_snapshot,
            )
            self._active_tasks[chat_id] = ActiveTelegramTask(
                chat_id=chat_id,
                task_label=task_label,
                request_summary=request_summary,
                app=active_task.app,
                thread_config=active_task.thread_config,
                stage=task_stage,
                backlog_item_id=backlog_item_id,
                cancellation_token=active_task.cancellation_token,
            )
            self._send_message(chat_id, pause_message)
            return

        logger.info("Telegram run: graph completed for chat %s; clearing active task.", chat_id)
        self._active_tasks.pop(chat_id, None)
        self._finalize_completed_task(chat_id, task_label, state_snapshot.values, backlog_item_id)

    def _handle_sleep(self, chat_id: str, argument: str) -> None:
        arg = argument.strip().lower()
        if arg not in ("on", "off"):
            self._send_message(chat_id, "Usage: /sleep on  or  /sleep off")
            return
        enable = arg == "on"
        new_settings = dc_replace(self._settings, sleep_mode=enable)
        save_settings(new_settings)
        self._settings = new_settings
        logger.info("[SLEEP] Sleep mode set to %s.", "ON" if enable else "OFF")
        if enable:
            self._send_message(
                chat_id,
                "Sleep mode ON.\n"
                "LOW and MEDIUM risk tasks will run automatically.\n"
                "HIGH risk and force-approval tasks will pause and notify you.",
            )
        else:
            self._send_message(chat_id, "Sleep mode OFF. All tasks will wait for your approval.")

    def _handle_cancel_code(self, chat_id: str) -> None:
        logger.info(
            "Telegram action: evaluating running coding-agent cleanup for chat %s.", chat_id
        )
        active_task = self._active_tasks.get(chat_id)
        if active_task is None or active_task.cancellation_token is None:
            logger.info(
                "Telegram action: cancel_code found no running coding-agent "
                "subprocess for chat %s.",
                chat_id,
            )
            self._send_message(chat_id, "No coding-agent subprocess is running.")
            return

        if active_task.stage != TelegramTaskStage.RUNNING:
            logger.info(
                "Telegram action: cancel_code ignored for chat %s because task %s "
                "is waiting on %s.",
                chat_id,
                active_task.task_label,
                active_task.stage,
            )
            self._send_message(
                chat_id,
                "The task is waiting for a decision; use /approve or /reject instead.",
            )
            return

        if active_task.cancellation_token.cancel():
            logger.info(
                "Telegram action: cancellation requested for chat %s; active task "
                "%s remains until the worker exits.",
                chat_id,
                active_task.task_label,
            )
            self._send_message(
                chat_id, "Cancellation requested for the running coding-agent subprocess."
            )
        else:
            logger.info(
                "Telegram action: cancel_code found no live subprocess to stop "
                "for chat %s; active task %s remains.",
                chat_id,
                active_task.task_label,
            )
            self._send_message(chat_id, "No live coding-agent subprocess was found to stop.")

    def _handle_list(self, chat_id: str, limit_arg: str) -> None:
        repository = self._get_repository()
        limit = _parse_backlog_limit(limit_arg, default_limit=10, allow_all=True)
        if limit == "all":
            items = repository.list_items_sorted()
            empty_message = "No backlog items."
        else:
            items = repository.list_open_items_sorted()[: min(max(limit, 1), 50)]
            empty_message = "No open backlog items."
        self._send_backlog_list(chat_id, items, empty_message=empty_message)

    def _handle_next(self, chat_id: str, limit_arg: str) -> None:
        repository = self._get_repository()
        limit = _parse_backlog_limit(limit_arg, default_limit=3, allow_all=False)
        items = repository.list_open_items_sorted()[: min(max(limit, 1), 20)]
        self._send_backlog_list(
            chat_id,
            items,
            empty_message="No open backlog items.",
            numbered=True,
        )

    def _send_backlog_list(
        self,
        chat_id: str,
        items: list[BacklogItem],
        *,
        empty_message: str,
        numbered: bool = False,
    ) -> None:
        if not items:
            self._send_message(chat_id, empty_message)
            return
        blocks = (
            _format_numbered_backlog_item_blocks(items)
            if numbered
            else [format_backlog_list_item(item) for item in items]
        )
        for message in _chunk_message_blocks(
            blocks,
            limit=self._settings.telegram_max_message_chars,
        ):
            self._send_message(chat_id, message)

    def _handle_count(self, chat_id: str) -> None:
        repository = self._get_repository()
        items = repository.list_open_items()
        if not items:
            self._send_message(chat_id, "No open backlog items.")
            return
        item_word = "item" if len(items) == 1 else "items"
        self._send_message(chat_id, f"Backlog has {len(items)} open {item_word}.")

    def _handle_read(self, chat_id: str, item_id: str) -> None:
        from .telegram_agent_graph import _backlog_body_without_status, _truncate_text

        repository = self._get_repository()
        try:
            item = repository.get_item(item_id)
        except (ValueError, FileNotFoundError) as error:
            self._send_message(chat_id, f"Not found: {error}")
            return
        body = _truncate_text(_backlog_body_without_status(item.body), 1600)
        lines = [f"{item.item_id} - {item.title}", f"Status: {item.status.value}"]
        if body:
            lines.extend(["", body])
        self._send_message(chat_id, "\n".join(lines))

    def _handle_set_status(self, chat_id: str, argument: str) -> None:
        item_id, new_status = argument.split(maxsplit=1)
        repository = self._get_repository()
        try:
            item = repository.update_item_status(item_id, new_status)
            self._send_message(
                chat_id,
                f"Updated {item.item_id} - {item.title}: Status is now '{item.status.value}'.",
            )
        except (ValueError, FileNotFoundError) as error:
            self._send_message(chat_id, f"Failed to update status: {error}")

    def _coding_agent_progress_callback(
        self,
        *,
        chat_id: str,
        request_summary: str,
    ) -> Callable[[str], None]:
        """Return a short operator-facing heartbeat for a long coding-agent run."""

        def _callback(message: str) -> None:
            self._send_message(
                chat_id,
                _truncate_text(message, self._settings.telegram_max_message_chars),
            )

        return _callback

    def _active_task_state_values(self, active_task: ActiveTelegramTask) -> dict[str, Any]:
        state_snapshot = active_task.app.get_state(active_task.thread_config)
        return dict(state_snapshot.values)

    def _finalize_completed_task(
        self,
        chat_id: str,
        task_label: str,
        state_values: dict[str, Any],
        backlog_item_id: str | None,
    ) -> None:
        coding_agent_success = bool(state_values.get("coding_agent_success", False))
        verification_status = str(state_values.get("verification_status", "")).strip()
        # The coding agent exiting cleanly is not enough — the backlog only closes once
        # the AI Tech Lead has verified the work against the approved task.
        success = coding_agent_success and verification_status == "complete"
        timed_out = bool(state_values.get("coding_agent_timed_out", False))
        backlog_status_line = "not tracked"
        backlog_update_alert: str | None = None

        if backlog_item_id:
            if success:
                backlog_updated, backlog_update_alert = self._close_backlog_item(
                    backlog_item_id,
                    state_values,
                )
                backlog_status_line = "Done" if backlog_updated else "unchanged (update failed)"
            else:
                backlog_status_line = "unchanged"

        completion_msg = _completion_message(
            task_label,
            state_values=state_values,
            backlog_status_line=backlog_status_line,
            backlog_update_alert=backlog_update_alert,
            timed_out=timed_out,
            limit=self._settings.telegram_max_message_chars,
        )

        self._send_message(chat_id, completion_msg)

    def _close_backlog_item(
        self,
        backlog_item_id: str,
        state_values: dict[str, Any],
    ) -> tuple[bool, str | None]:
        from datetime import date as _date

        changed_files: tuple[str, ...] = state_values.get("coding_agent_changed_files", ())
        result_text = str(state_values.get("coding_agent_result", "")).strip()
        duration_line = next(
            (line for line in result_text.splitlines() if line.startswith("Duration:")), ""
        )

        parts = [f"Completed {_date.today().isoformat()}."]
        if duration_line:
            parts.append(duration_line + ".")
        if changed_files:
            files_summary = ", ".join(changed_files[:10])
            parts.append(f"Changed files: {files_summary}.")
        else:
            parts.append("No files changed.")

        validation_note = " ".join(parts)
        request_id = _telegram_backlog_request_id(backlog_item_id)

        try:
            repository = self._get_repository()
            runtime_store = BacklogRuntimeStore()
            snapshot = runtime_store.get_snapshot(request_id)
            if snapshot is not None:
                expected_row_hash = snapshot.row_hash
            else:
                # No snapshot (process restarted since /run, or an
                # out-of-band completion): treat the current row as the
                # expected source rather than refusing to close the item.
                expected_row_hash = repository.get_item_with_source(backlog_item_id).row_hash

            sync_status = enqueue_and_flush_update(
                runtime_store=runtime_store,
                sheets_repository=repository,
                request_id=request_id,
                item_id=backlog_item_id,
                update_fields={
                    "Status": BacklogStatus.DONE.value,
                    "Evidence / Validation": validation_note,
                },
                expected_row_hash=expected_row_hash,
                max_attempts=self._settings.backlog_pending_update_max_attempts,
            )
        except Exception as exc:
            alert = f"ALERT: Could not update backlog for {backlog_item_id}: {exc}"
            logger.error("Finalize: %s", alert)
            return False, alert

        runtime_store.mark_snapshot_status(
            request_id, "completed" if sync_status == "synced" else sync_status
        )

        if sync_status == "synced":
            logger.info("Finalize: %s marked Done in backlog.", backlog_item_id)
            return True, None
        if sync_status == "conflict":
            alert = (
                f"ALERT: {backlog_item_id} changed in the Sheet while this task ran. "
                "Status/evidence were NOT written; see sync_conflicts for review."
            )
            logger.warning("Finalize: %s", alert)
            return False, alert
        alert = (
            f"ALERT: Backlog update for {backlog_item_id} is queued "
            f"(sync status: {sync_status})."
        )
        logger.warning("Finalize: %s", alert)
        return False, alert

    def _orchestrator_input_expectation_message(
        self,
        *,
        active_task: ActiveTelegramTask,
        state_values: dict[str, Any],
    ) -> str:
        lines = [f"Task waiting for your input: {active_task.task_label}."]
        pending_question = str(state_values.get("orchestrator_input_question", "")).strip()
        pending_reason = str(state_values.get("orchestrator_input_reason", "")).strip()
        if pending_question:
            lines.append(f"Waiting for: {pending_question}")
        elif pending_reason:
            lines.append(f"Why it is waiting: {pending_reason}")
        else:
            lines.append(f"Current request: {active_task.request_summary}.")
        lines.append("Still expected: reply with the missing detail, /approve, or /reject.")
        return "\n".join(lines)

    def _orchestrator_input_clarification_ack(
        self,
        *,
        active_task: ActiveTelegramTask,
        state_values: dict[str, Any],
        text: str,
    ) -> str:
        preview = _summarize_text(text, limit=120)
        expected = self._orchestrator_input_expectation_message(
            active_task=active_task,
            state_values=state_values,
        )
        return "\n".join(
            [
                f"Saved for this task: {preview}",
                expected,
            ]
        )

    def _orchestrator_input_question_answer(
        self,
        *,
        active_task: ActiveTelegramTask,
        state_values: dict[str, Any],
    ) -> str:
        lines = [f"Task waiting for your input: {active_task.task_label}."]
        lines.append(f"Request: {active_task.request_summary}.")

        approval_reason = str(state_values.get("approval_reason", "")).strip()
        if approval_reason:
            lines.append(f"Current reason: {approval_reason}")

        orchestrator_question = str(state_values.get("orchestrator_input_question", "")).strip()
        if orchestrator_question:
            lines.append(f"Current question: {orchestrator_question}")

        feedback_count = len(list(state_values.get("task_feedback", [])))
        if feedback_count:
            lines.append(f"Saved feedback items: {feedback_count}.")

        lines.append("Still expected: reply with the missing detail, /approve, or /reject.")
        return "\n".join(lines)

    def _handle_pre_run_approval(
        self,
        chat_id: str,
        *,
        sender: str,
        action: str,
        text: str = "",
    ) -> bool:
        """Handle a human decision on a paused pre-run, research, or completion-verification interrupt.

        `action` is one of "approve", "request_changes", "ask_question", "cancel".
        `request_changes` and `ask_question` only apply to the PRE_RUN_APPROVAL
        stage (the coding-workflow's `4_approval_interrupt`) — RESEARCH_APPROVAL and
        COMPLETION_VERIFICATION keep their own binary approve/cancel contract, with
        `text` carrying the rejection reason for COMPLETION_VERIFICATION.
        """
        active_task = self._active_tasks.get(chat_id)
        if active_task is None:
            logger.warning(
                "Telegram approval: no active task found for chat %s (sender=%s). "
                "Possible cause: process restarted mid-task and lost in-memory state, "
                "or the task already completed/was cancelled before this command arrived.",
                chat_id,
                sender,
            )
            return False

        if action != "cancel" and active_task.stage not in {
            TelegramTaskStage.PRE_RUN_APPROVAL,
            TelegramTaskStage.RESEARCH_APPROVAL,
            TelegramTaskStage.COMPLETION_VERIFICATION,
        }:
            logger.warning(
                "Telegram approval: /%s received for chat %s but task '%s' is in stage=%s, "
                "not an approval-waiting stage. "
                "Expected PRE_RUN_APPROVAL or RESEARCH_APPROVAL. "
                "Possible cause: task is still running (RUNNING stage) or already past approval.",
                action,
                chat_id,
                active_task.task_label,
                active_task.stage,
            )
            return False

        if action in {"request_changes", "ask_question"} and (
            active_task.stage != TelegramTaskStage.PRE_RUN_APPROVAL
        ):
            self._send_message(
                chat_id,
                f"/{'request_changes' if action == 'request_changes' else 'ask'} is not "
                "available for this approval step. Use /approve or /cancel.",
            )
            return True

        if action == "cancel":
            if active_task.stage == TelegramTaskStage.COMPLETION_VERIFICATION:
                reason = text.strip() or "Rejected without a stated reason."
                resume_value: dict[str, Any] = {"decision": "reject", "text": reason}
                self._send_message(chat_id, "Noted — reporting this as unresolved...")
            elif active_task.stage != TelegramTaskStage.PRE_RUN_APPROVAL:
                # Unchanged existing behaviour for other stages: discard without resuming.
                self._active_tasks.pop(chat_id, None)
                logger.info(
                    "Telegram approval: task cancelled and closed: %s", active_task.task_label
                )
                self._send_message(chat_id, f"Task cancelled and closed: {active_task.task_label}")
                return True
            else:
                resume_value = {"action": "cancel"}
                self._send_message(chat_id, "Cancelled. Stopping the task...")
        elif action == "approve":
            if active_task.stage == TelegramTaskStage.COMPLETION_VERIFICATION:
                resume_value = {"decision": "confirm_complete"}
                self._send_message(chat_id, "Confirmed complete. Closing the task...")
            else:
                resume_value = (
                    {"approved": True}
                    if active_task.stage == TelegramTaskStage.RESEARCH_APPROVAL
                    else {"action": "approve", "approved_by": sender}
                )
                self._send_message(chat_id, "Approved. Resuming...")
        elif action == "request_changes":
            feedback = text.strip()
            if not feedback:
                self._send_message(
                    chat_id,
                    "/request_changes requires feedback text, e.g. "
                    "/request_changes Keep this to docs only.",
                )
                return True
            resume_value = {"action": "request_changes", "feedback": feedback}
            self._send_message(chat_id, "Got it — sending feedback for a revised proposal...")
        else:  # action == "ask_question"
            question = text.strip()
            if not question:
                self._send_message(
                    chat_id,
                    "/ask requires a question, e.g. /ask Which files will this touch?",
                )
                return True
            resume_value = {"action": "ask_question", "question": question}
            self._send_message(chat_id, "Let me check on that...")

        logger.info(
            "Telegram approval: resuming task %s with action=%s (stage=%s sender=%s)",
            active_task.task_label,
            action,
            active_task.stage,
            sender,
        )
        try:
            active_task.app.invoke(Command(resume=resume_value), config=active_task.thread_config)
            state_snapshot = active_task.app.get_state(active_task.thread_config)
        except Exception:
            logger.exception(
                "Failed to resume Telegram task after %s from %s: %s",
                action,
                sender,
                active_task.task_label,
            )
            self._active_tasks.pop(chat_id, None)
            raise

        if state_snapshot.next:
            next_stage, pause_message = self._stage_and_message_from_snapshot(
                task_label=active_task.task_label,
                request_summary=active_task.request_summary,
                state_snapshot=state_snapshot,
            )
            self._active_tasks[chat_id] = ActiveTelegramTask(
                chat_id=chat_id,
                task_label=active_task.task_label,
                request_summary=active_task.request_summary,
                app=active_task.app,
                thread_config=active_task.thread_config,
                stage=next_stage,
                backlog_item_id=active_task.backlog_item_id,
            )
            self._send_message(chat_id, pause_message)
            return True

        self._active_tasks.pop(chat_id, None)
        self._finalize_completed_task(
            chat_id, active_task.task_label, state_snapshot.values, active_task.backlog_item_id
        )
        return True

    def _resume_from_clarification(
        self, chat_id: str, active_task: ActiveTelegramTask, text: str
    ) -> None:
        """Resume the graph with the human's clarification text, handling loops and completion."""

        logger.info(
            "Telegram clarification: resuming task %s with text length=%d",
            active_task.task_label,
            len(text),
        )
        try:
            active_task.app.invoke(Command(resume=text), config=active_task.thread_config)
            state_snapshot = active_task.app.get_state(active_task.thread_config)
        except Exception:
            logger.exception(
                "Failed to resume from clarification for task: %s", active_task.task_label
            )
            self._active_tasks.pop(chat_id, None)
            raise

        if not state_snapshot.next:
            self._active_tasks.pop(chat_id, None)
            self._finalize_completed_task(
                chat_id, active_task.task_label, state_snapshot.values, active_task.backlog_item_id
            )
            return

        task_stage, pause_message = self._stage_and_message_from_snapshot(
            task_label=active_task.task_label,
            request_summary=active_task.request_summary,
            state_snapshot=state_snapshot,
        )
        self._active_tasks[chat_id] = ActiveTelegramTask(
            chat_id=chat_id,
            task_label=active_task.task_label,
            request_summary=active_task.request_summary,
            app=active_task.app,
            thread_config=active_task.thread_config,
            stage=task_stage,
            backlog_item_id=active_task.backlog_item_id,
        )
        self._send_message(chat_id, pause_message)

    def _stage_and_message_from_snapshot(
        self,
        *,
        task_label: str,
        request_summary: str,
        state_snapshot: Any,
    ) -> tuple[TelegramTaskStage, str]:
        """Derive the task stage and message from a paused interrupt snapshot."""

        interrupt_value = _interrupt_from_snapshot(state_snapshot)
        kind = interrupt_value.get("kind", "") if isinstance(interrupt_value, dict) else ""
        logger.info(
            "Telegram stage detection: interrupt kind=%s task=%s", kind or "none", task_label
        )

        if kind == "research_approval":
            question = str(interrupt_value.get("question", "")).strip()
            message = question or (
                "This task may need online research. "
                "Do you want me to research online before continuing? "
                "Reply /approve to proceed or /reject to abort."
            )
            return TelegramTaskStage.RESEARCH_APPROVAL, message

        if kind == "clarification":
            question = str(interrupt_value.get("question", "")).strip()
            message = question or "I need more information before I can proceed."
            return TelegramTaskStage.ORCHESTRATOR_INPUT, message

        if kind == "plan_guidance":
            plan_text = str(interrupt_value.get("plan_text", "")).strip()
            reason = str(interrupt_value.get("reason", "")).strip()
            rejection_count = int(interrupt_value.get("rejection_count", 0))
            sections = [f"Plan rejected ({rejection_count}x): {task_label}"]
            if reason:
                sections.append(f"Reason: {_summarize_text(reason, limit=160)}")
            if plan_text:
                sections.append(f"Plan summary: {_summarize_text(plan_text, limit=200)}")
            sections.append("Reply with guidance to retry, or /reject to abort.")
            return TelegramTaskStage.ORCHESTRATOR_INPUT, "\n\n".join(sections)

        if kind == "failure_guidance":
            retry_count = int(interrupt_value.get("retry_count", 0))
            result_summary = str(interrupt_value.get("coding_agent_result", "")).strip()
            sections = [f"Coding agent failed {retry_count}x: {task_label}"]
            if result_summary:
                sections.append(f"Result: {_summarize_text(result_summary, limit=200)}")
            sections.append("Reply with guidance to retry, or /reject to abort.")
            return TelegramTaskStage.ORCHESTRATOR_INPUT, "\n\n".join(sections)

        if kind == "approval":
            approval_reason = str(interrupt_value.get("reason", "")).strip()
            formulated_task = str(interrupt_value.get("formulated_task", "")).strip()
            last_question = str(interrupt_value.get("last_question", "")).strip()
            last_answer = str(interrupt_value.get("last_answer", "")).strip()
            message = _approval_prompt(
                task_label=task_label,
                request_summary=request_summary,
                approval_reason=approval_reason,
                formulated_task=formulated_task,
                last_question=last_question,
                last_answer=last_answer,
                limit=self._settings.telegram_max_message_chars,
            )
            return TelegramTaskStage.PRE_RUN_APPROVAL, message

        if kind == "completion_verification":
            reason = str(interrupt_value.get("reason", "")).strip()
            sections = [f"Can't verify this is fully done: {task_label}"]
            if reason:
                sections.append(_summarize_text(reason, limit=200))
            sections.append("Reply /approve to confirm complete, or /reject <reason> to flag it.")
            return TelegramTaskStage.COMPLETION_VERIFICATION, "\n\n".join(sections)

        # Fallback: unrecognised interrupt kind — use state values for best-effort message
        logger.warning(
            "Telegram stage detection: unrecognised interrupt kind=%r for task %s", kind, task_label
        )
        approval_reason = str(state_snapshot.values.get("approval_reason", "")).strip()
        formulated_task = str(state_snapshot.values.get("formulated_task", "")).strip()
        message = _approval_prompt(
            task_label=task_label,
            request_summary=request_summary,
            approval_reason=approval_reason,
            formulated_task=formulated_task,
            limit=self._settings.telegram_max_message_chars,
        )
        return TelegramTaskStage.PRE_RUN_APPROVAL, message

    def _status_text(self, chat_id: str) -> str:
        execution_state = "ENABLED" if self._coding_agent_execution_enabled() else "DISABLED"
        telegram_state = "ENABLED" if self._settings.telegram_enabled else "DISABLED"
        transport_state = self._settings.telegram_transport.upper()
        active_task = self._active_tasks.get(chat_id)
        active_chat_count = len(self._active_tasks)
        pending_backlog_count = len(self._pending_backlog_drafts)

        sleep_state = "ON" if self._settings.sleep_mode else "OFF"
        lines = [
            (f"Bot is alive. Telegram: {telegram_state}. "),
            f"Transport: {transport_state}.",
            f"Coding-agent execution: {execution_state}.",
            f"Sleep mode: {sleep_state}.",
            f"Orchestrator AI model: {self._settings.orchestrator_ai_model}.",
            f"Active coding tasks: {active_chat_count}.",
            f"Pending backlog refinements: {pending_backlog_count}.",
        ]
        if active_task is None:
            lines.append("This chat: no active task.")
        else:
            lines.append(f"This chat: {active_task.task_label}")
            lines.append("Reply with a clarification, /approve, or /reject.")

        return "\n".join(lines)

    def _handle_new_session(self, chat_id: str) -> None:
        active_task = self._active_tasks.pop(chat_id, None)
        pending_draft = self._pending_backlog_drafts.pop(chat_id, None)
        old_session_id = self._session_id
        self._reset_telegram_agent_memory()
        self._telegram_agent_session_cost_usd = 0.0
        self._session_id = uuid.uuid4().hex

        cancellation_requested = False
        if active_task is not None and active_task.cancellation_token is not None:
            cancellation_requested = active_task.cancellation_token.cancel()

        cleaned_parts: list[str] = []
        if active_task is not None:
            cleaned_parts.append(f"active task {active_task.task_label}")
        if pending_draft is not None:
            cleaned_parts.append(f"pending backlog draft {pending_draft.draft.item_id}")

        if not cleaned_parts:
            logger.info(
                "Telegram action: fresh session started for chat %s. Project root %s "
                "(hardcoded default for now). Cleared nothing; reset Telegram agent memory "
                "and session id %s -> %s.",
                chat_id,
                self._settings.project_root,
                old_session_id,
                self._session_id,
            )
            self._send_message(chat_id, self._fresh_session_text())
            return

        logger.info(
            "Telegram action: fresh session started for chat %s after discarding "
            "%s. Project root %s (hardcoded default for now). Reset Telegram agent memory "
            "and session id %s -> %s.",
            chat_id,
            ", ".join(cleaned_parts),
            self._settings.project_root,
            old_session_id,
            self._session_id,
        )
        lines = [
            "Fresh session ready.",
        ]
        if active_task is not None:
            lines.append(f"Discarded active task: {active_task.task_label}.")
        if pending_draft is not None:
            lines.append(f"Discarded pending backlog draft: {pending_draft.draft.item_id}.")
        if cancellation_requested:
            lines.append("Cancellation requested for the running coding-agent subprocess.")
        elif active_task is not None and active_task.stage == TelegramTaskStage.RUNNING:
            lines.append("No live coding-agent subprocess was found to stop.")
        lines.extend(self._session_intro_lines())
        self._send_message(chat_id, "\n".join(lines))

    def _help_text(self) -> str:
        identity = _load_orchestrator_identity_name()
        execution_state = "enabled" if self._coding_agent_execution_enabled() else "disabled"
        lines = [
            identity,
            f"Orchestrator AI model: {self._settings.orchestrator_ai_model} ({execution_state}).",
            "Commands:",
        ]
        for section_title, commands in CANONICAL_BOT_COMMAND_SECTIONS:
            lines.append(f"{section_title}:")
            lines.extend(f"  {cmd.help_line()}" for cmd in commands)
        return "\n".join(lines)

    def _fresh_session_text(self) -> str:
        return "\n".join(
            [
                "Fresh session ready.",
                f"Project root: {self._settings.project_root} (hardcoded default for now).",
                f"Model: {self._settings.orchestrator_ai_model}.",
                "Use /help for commands.",
            ]
        )

    def _session_intro_lines(self) -> list[str]:
        execution_state = "enabled" if self._coding_agent_execution_enabled() else "disabled"
        return [
            f"Project root: {self._settings.project_root} (hardcoded default for now).",
            f"Orchestrator AI model: {self._settings.orchestrator_ai_model} ({execution_state}).",
            "Use /propose <text> to refine a backlog idea, /code for explicit "
            "coding work, or /run to implement an existing backlog item.",
        ]

    def _get_telegram_agent_app(self) -> Any:
        if self._telegram_agent_app is None:
            self._telegram_agent_app = build_telegram_agent_graph(
                settings=self._settings,
                checkpointer=get_checkpointer(),
            )
        return self._telegram_agent_app

    def _telegram_agent_thread_id(self, chat_id: str) -> str:
        return f"telegram-agent-{self._session_id}-{chat_id}"

    def _reset_telegram_agent_memory(self) -> None:
        self._telegram_agent_app = None

    def _record_telegram_agent_usage(self, reply: TelegramAgentReply) -> None:
        """Accumulate per-session Telegram agent LLM cost."""

        usage_cost_usd = getattr(reply, "usage_cost_usd", None)
        if usage_cost_usd is None:
            return
        self._telegram_agent_session_cost_usd += usage_cost_usd

    def _get_repository(self):
        return repository_from_settings(self._settings)

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

    def _register_commands(self) -> None:
        commands = [cmd.to_api_dict() for cmd in CANONICAL_BOT_COMMANDS]
        try:
            self._client.delete_my_commands()
            self._client.set_my_commands(commands)
            logger.info(
                "Telegram command menu registered: %s",
                [c["command"] for c in commands],
            )
        except Exception:
            logger.warning(
                "Failed to register Telegram command menu; continuing startup.",
                exc_info=True,
            )

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
        threading.Thread(target=self._handle_update, args=(update,), daemon=True).start()


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
    if command_name in {"start", "commands"}:
        command_name = TelegramCommandName.HELP
    if command_name not in TelegramCommandName._value2member_map_:
        return TelegramCommand(name=TelegramCommandName.UNKNOWN, raw_text=normalized_text)

    argument = normalized_text[len(command_text) :].lstrip()

    if command_name in {
        TelegramCommandName.HELP,
        TelegramCommandName.STATUS,
        TelegramCommandName.CANCEL_CODE,
        TelegramCommandName.APPROVE,
        TelegramCommandName.REJECT,
        TelegramCommandName.CANCEL,
        TelegramCommandName.COUNT,
    }:
        if argument:
            raise ValueError(f"/{command_name} does not accept extra text.")
        return TelegramCommand(
            name=TelegramCommandName(command_name),
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.REQUEST_CHANGES:
        if not argument.strip():
            raise ValueError("/request_changes requires feedback text.")
        return TelegramCommand(
            name=TelegramCommandName.REQUEST_CHANGES,
            argument=argument,
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.ASK:
        if not argument.strip():
            raise ValueError("/ask requires a question.")
        return TelegramCommand(
            name=TelegramCommandName.ASK,
            argument=argument,
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.NEW:
        if argument.strip():
            raise ValueError(
                "Use /propose <text> to create a backlog draft. /new only resets the session."
            )
        return TelegramCommand(name=TelegramCommandName.NEW, raw_text=normalized_text)

    if command_name == TelegramCommandName.PROPOSE:
        if not argument.strip():
            raise ValueError("/propose requires an idea description.")
        return TelegramCommand(
            name=TelegramCommandName.PROPOSE,
            argument=argument,
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

    if command_name == TelegramCommandName.NEXT:
        limit_text = argument.strip()
        if limit_text and not re.fullmatch(r"\d+", limit_text):
            raise ValueError("/next accepts an optional positive integer limit.")
        return TelegramCommand(
            name=TelegramCommandName.NEXT,
            argument=limit_text,
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.CODE:
        if not argument.strip():
            raise ValueError(f"/{command_name} requires a short task description.")
        return TelegramCommand(
            name=TelegramCommandName(command_name),
            argument=argument,
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.LIST:
        limit_text = argument.strip()
        if limit_text and limit_text != "all" and not re.fullmatch(r"\d+", limit_text):
            raise ValueError("/list accepts an optional positive integer limit or 'all'.")
        return TelegramCommand(
            name=TelegramCommandName.LIST,
            argument=limit_text,
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.READ:
        item_id = argument.splitlines()[0].strip().upper() if argument else ""
        if not re.fullmatch(r"[A-Z]+-\d{3}", item_id):
            raise ValueError("/read requires a backlog item ID like ATL-001.")
        return TelegramCommand(
            name=TelegramCommandName.READ,
            argument=item_id,
            raw_text=normalized_text,
        )

    if command_name == TelegramCommandName.SET_STATUS:
        parts = argument.strip().split(maxsplit=1) if argument.strip() else []
        if len(parts) < 2 or not parts[1].strip():
            raise ValueError(
                "/set_status requires a backlog ID and a status, e.g. /set_status ATL-001 Done."
            )
        item_id = parts[0].upper()
        if not re.fullmatch(r"[A-Z]+-\d{3}", item_id):
            raise ValueError("/set_status requires a valid backlog ID like ATL-001.")
        status = normalize_backlog_status(parts[1])
        if status is None:
            raise ValueError(f"/set_status requires one of: {backlog_status_choices()}.")
        return TelegramCommand(
            name=TelegramCommandName.SET_STATUS,
            argument=f"{item_id} {status.value}",
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


def _parse_backlog_limit(
    limit_arg: str,
    *,
    default_limit: int,
    allow_all: bool,
) -> int | str:
    limit_text = limit_arg.strip()
    if not limit_text:
        return default_limit
    if allow_all and limit_text == "all":
        return "all"
    if not re.fullmatch(r"\d+", limit_text):
        raise ValueError("Backlog limit must be a positive integer.")
    return int(limit_text)


def _format_numbered_backlog_item_blocks(items: list[BacklogItem]) -> list[str]:
    blocks: list[str] = []
    for index, item in enumerate(items, start=1):
        lines = format_backlog_list_item(item).splitlines()
        if not lines:
            continue
        numbered_lines = [f"{index}. {lines[0]}"]
        numbered_lines.extend(f"   {line}" for line in lines[1:])
        blocks.append("\n".join(numbered_lines))
    return blocks


def _chunk_message_blocks(blocks: list[str], *, limit: int) -> list[str]:
    if limit <= 0:
        raise ValueError("Message limit must be positive.")

    messages: list[str] = []
    current_blocks: list[str] = []
    current_length = 0

    for block in blocks:
        block_length = len(block)
        if not current_blocks:
            if block_length > limit:
                messages.append(_truncate_text(block, limit))
                continue
            current_blocks.append(block)
            current_length = block_length
            continue

        candidate_length = current_length + 2 + block_length
        if candidate_length > limit:
            messages.append("\n\n".join(current_blocks))
            current_blocks = []
            current_length = 0
            if block_length > limit:
                messages.append(_truncate_text(block, limit))
                continue
            current_blocks.append(block)
            current_length = block_length
            continue

        current_blocks.append(block)
        current_length = candidate_length

    if current_blocks:
        messages.append("\n\n".join(current_blocks))

    return messages


def run_telegram_operator(*, execute_coding_agent_override: bool | None = None) -> None:
    """Start the configured Telegram transport using the current settings."""

    settings = load_settings()
    if not settings.telegram_enabled:
        logger.info("Telegram operator disabled by settings.")
        return

    try:
        outcomes = run_pending_backlog_recovery(settings)
        if outcomes:
            logger.info("Backlog sync recovery at startup: %s", outcomes)
    except Exception as exc:
        logger.warning("Backlog sync recovery at startup failed (will retry later): %s", exc)

    operator = TelegramOperator(
        get_telegram_bot_token(),
        settings,
        execute_coding_agent_override=execute_coding_agent_override,
    )
    operator.run()


def _base_graph_state(request: str) -> GraphState:
    return build_initial_graph_state(request)


def _approval_prompt(
    task_label: str,
    request_summary: str,
    approval_reason: str,
    *,
    formulated_task: str = "",
    last_question: str = "",
    last_answer: str = "",
    limit: int,
) -> str:
    reason = _telegram_approval_reason(approval_reason)
    sections = [f"Approval needed\n{request_summary}", f"Risk: {reason}"]
    if last_question:
        sections.append(
            f"Q: {_summarize_text(last_question, limit=200)}\n"
            f"A: {_summarize_text(last_answer, limit=400)}"
        )
    sections.append(
        "/approve — continue\n"
        "/request_changes <feedback> — send feedback for a revised proposal\n"
        "/ask <question> — ask a question before deciding\n"
        "/cancel — stop this task"
    )
    return _truncate_text("\n\n".join(sections), limit)


def _telegram_approval_reason(approval_reason: str) -> str:
    normalized_reason = " ".join(approval_reason.split())
    if (
        "not configured" in normalized_reason.lower()
        or "ai risk review is off" in normalized_reason.lower()
    ):
        return "AI risk review is off — manual approval required."
    if "failed" in normalized_reason.lower() or "invalid risk review" in normalized_reason.lower():
        return "AI risk review failed — manual approval required."
    if "low confidence" in normalized_reason.lower():
        return "AI was not confident enough to auto-approve."
    if "interrupt before implementation" in normalized_reason.lower():
        return "This backlog item is flagged to require approval before running."
    if normalized_reason.startswith("Orchestrator AI risk review:"):
        normalized_reason = normalized_reason.removeprefix("Orchestrator AI risk review:").strip()
    return _summarize_text(normalized_reason, limit=160)


def _telegram_preview(text: str, *, limit: int) -> str:
    """Return a compact single-line preview suitable for Telegram messages."""
    normalized = " ".join(text.split())
    return _truncate_text(normalized, limit)


def _backlog_refinement_prompt(
    draft: BacklogRefinementDraft,
    *,
    source: str,
    limit: int,
) -> str:
    approval_required = "yes" if draft.approval_required else "no"
    priority = draft.priority.strip() or "Unspecified"
    research_required = "yes" if draft.research_required else "no"
    external_research_needed = "yes" if draft.external_research_needed else "no"
    duplicate_summary = _summarize_text(draft.duplicate_check_result, limit=180)
    stale_summary = _summarize_text(draft.stale_check_result, limit=180)
    already_done_summary = _summarize_text(draft.already_done_check_result, limit=180)
    cache_used = "; ".join(draft.research_cache_used) if draft.research_cache_used else "None"
    pattern = _summarize_text(draft.recommended_implementation_pattern, limit=220)
    guidance = _summarize_text(draft.implementation_guidance, limit=220)
    approval_reason = _summarize_text(draft.approval_reason, limit=180)
    problem_summary = _summarize_text(draft.problem, limit=200)
    outcome_summary = _summarize_text(draft.desired_outcome, limit=180)
    risk_flags = "; ".join(draft.approval_risk_flags) if draft.approval_risk_flags else "None"
    return _truncate_text(
        "\n".join(
            [
                "Backlog refinement ready",
                f"ID: {draft.item_id}",
                f"Title: {draft.title}",
                f"Type: {draft.item_type}  Epic: {draft.epic}  Size: {draft.size}",
                f"Source: {source}  Priority: {priority}",
                f"Problem: {problem_summary}",
                f"Outcome: {outcome_summary}",
                f"Approval required: {approval_required}",
                f"Approval reason: {approval_reason}",
                f"Risk flags: {risk_flags}",
                f"Research required: {research_required}",
                f"Research cache used: {cache_used}",
                f"External research needed: {external_research_needed}",
                f"Duplicate check: {duplicate_summary}",
                f"Stale check: {stale_summary}",
                f"Already done check: {already_done_summary}",
                f"Recommended pattern: {pattern}",
                f"Implementation guidance: {guidance}",
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
    timed_out: bool = False,
    backlog_status_line: str | None = None,
    backlog_update_alert: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    coding_agent_result = str(state_values.get("coding_agent_result", "")).strip()
    verification_status = str(state_values.get("verification_status", "")).strip()
    verification_reason = str(state_values.get("verification_reason", "")).strip()
    if timed_out:
        header = f"Task timed out: {task_label}"
    elif verification_status == "failed":
        header = f"Task needs attention: {task_label}"
    else:
        header = f"Task complete: {task_label}"
    if not coding_agent_result:
        return _truncate_text(header, limit)

    # Completion summary rules live in data/prompts.json under completion_summary.
    # summary() format: friendly message, duration, changed files, token notice, then
    # Command:/Return code:/Timed out:/Stdout:/Stderr: (verbose, not shown on Telegram).
    _VERBOSE_PREFIXES = ("Command:", "Return code:", "Timed out:", "Stdout:", "Stderr:")
    summary_lines: list[str] = []
    for line in coding_agent_result.splitlines():
        if line.startswith(_VERBOSE_PREFIXES):
            break
        summary_lines.append(line)

    body = "\n".join(summary_lines).strip() or coding_agent_result.splitlines()[0]
    lines = [header, body]
    if verification_status == "failed" and verification_reason:
        lines.append(f"Verification: {_summarize_text(verification_reason, limit=200)}")
    if backlog_status_line:
        lines.append(f"Backlog: {backlog_status_line}")
    if backlog_update_alert:
        lines.append(backlog_update_alert)
    if extra_lines:
        lines.extend(extra_lines)
    return _truncate_text("\n".join(line for line in lines if line), limit)


def _load_orchestrator_identity_name(content: str | None = None) -> str:
    if content is None:
        if not ORCHESTRATOR_IDENTITY_PATH.exists():
            raise FileNotFoundError(
                f"Orchestrator identity file not found: {ORCHESTRATOR_IDENTITY_PATH}"
            )
        content = ORCHESTRATOR_IDENTITY_PATH.read_text(encoding="utf-8")

    name = _extract_markdown_section(content, "Name")
    return name


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


def _telegram_backlog_request_id(item_id: str) -> str:
    """Snapshot/outbox key for a Telegram-initiated backlog run.

    Telegram has no separate Hub-style request_id for backlog runs, and only
    one /run per item is meaningful at a time, so the item id itself keys
    the snapshot (namespaced to avoid collision with Hub-issued request ids).
    """
    return f"telegram:{item_id}"


def _backlog_start_message(item: BacklogItem, *, limit: int) -> str:
    """Short summary shown to the operator when a backlog task starts."""
    lines = [f"{item.item_id} — {item.title}"]
    goal = _extract_body_section(item.body, "Goal")
    if goal:
        lines.append(_summarize_text(goal, limit=200))
    return _truncate_text("\n".join(lines), limit)


def _backlog_request_summary(item: BacklogItem, *, limit: int = 120) -> str:
    """Single-line summary used for Telegram progress updates."""
    return _truncate_text(f"{item.item_id} - {item.title}", limit)


def _extract_body_section(body: str, section_name: str) -> str:
    """Return the text content of a named section (e.g. 'Goal:') from a backlog item body."""
    marker = f"{section_name}:"
    start = body.find(marker)
    if start == -1:
        return ""
    content_start = start + len(marker)
    next_blank = body.find("\n\n", content_start)
    raw = (
        body[content_start:next_blank].strip() if next_blank != -1 else body[content_start:].strip()
    )
    return " ".join(raw.split())


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
