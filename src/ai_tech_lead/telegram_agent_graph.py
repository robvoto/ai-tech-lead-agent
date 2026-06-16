"""Small Module-3-style Telegram agent graph.

This graph is deliberately limited to read-only backlog assistance. It follows the
LangChain Academy Module 3 pattern:
- MessagesState holds the conversation for a thread.
- One assistant node calls an LLM with bound tools.
- ToolNode executes safe tools.
- tools_condition routes assistant -> tools or assistant -> END.
- tools -> assistant lets the LLM use tool results in its final reply.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.backlog_repository import MarkdownBacklogRepository, format_backlog_list_item
from ai_tech_lead.backlog_store import SqliteBacklogRepository
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.env_loader import load_local_env
from ai_tech_lead.prompt_loader import TELEGRAM_AGENT_SYSTEM_PROMPT_KEY, load_prompt

logger = logging.getLogger(__name__)

_LLM_PRICING_PATH = PROJECT_ROOT / "config" / "llm_pricing.json"
# Cached after first load — pricing file is read once per process.
_pricing_cache: dict[str, tuple[float, float]] | None = None

@dataclass(frozen=True)
class TelegramAgentReply:
    """Final reply produced by the Telegram agent graph."""

    text: str
    truncated: bool = False
    usage_cost_usd: float | None = None


def build_telegram_agent_graph(*, settings: AppSettings, checkpointer: Any):
    """Build a read-only Telegram agent graph using backlog tools."""

    load_local_env()
    tools = _build_backlog_tools(settings)

    try:
        from langmem import create_manage_memory_tool, create_search_memory_tool

        from .knowledge_store import get_knowledge_store

        store = get_knowledge_store()
        memory_tools = [
            create_manage_memory_tool(
                ("coding", "learnings"),
                store=store,
                instructions="Save patterns, decisions, and tech insights useful across future sessions.",
            ),
            create_search_memory_tool(
                ("shared", "docs"),
                store=store,
                instructions="Search indexed project documentation and architecture notes.",
            ),
            create_search_memory_tool(
                ("shared", "trusted"),
                store=store,
                name="search_trusted_sources",
                instructions="Search trusted online sources (LangChain, LangGraph, Anthropic docs).",
            ),
        ]
        tools = [*tools, *memory_tools]
        logger.info("Knowledge store memory tools attached to Telegram agent graph.")
    except Exception as exc:
        logger.warning("Knowledge store unavailable, running without memory tools: %s", exc)

    logger.info(
        "Building Telegram agent graph with model=%s max_completion_tokens=%s timeout_seconds=%s",
        settings.orchestrator_ai_model,
        settings.orchestrator_ai_max_output_tokens,
        settings.orchestrator_ai_timeout_seconds,
    )
    llm = ChatOpenAI(
        model=settings.orchestrator_ai_model,
        # Keep the reply budget configurable from validated settings.
        # This ChatOpenAI version expects max_completion_tokens.
        max_completion_tokens=settings.orchestrator_ai_max_output_tokens,
        timeout=settings.orchestrator_ai_timeout_seconds,
    )
    llm_with_tools = llm.bind_tools(tools)
    system_message = SystemMessage(content=load_prompt(TELEGRAM_AGENT_SYSTEM_PROMPT_KEY))

    def assistant(state: MessagesState) -> dict[str, list[BaseMessage]]:
        if state["messages"] and isinstance(state["messages"][-1], ToolMessage):
            logger.info("[LEARN] Backlog tool returned results to the assistant node.")
        logger.info("[LEARN] Assistant node is calling the LLM.")
        logger.info("[LEARN] The LLM may answer directly or call a tool (tools node).")
        t0 = time.perf_counter()
        response = llm_with_tools.invoke([system_message, *state["messages"]])
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.info("LLM call elapsed: %.0fms", elapsed_ms)
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("assistant", assistant)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "assistant")
    builder.add_conditional_edges("assistant", tools_condition)
    builder.add_edge("tools", "assistant")
    return builder.compile(checkpointer=checkpointer)


def run_telegram_agent_message(
    *,
    app: Any,
    thread_id: str,
    text: str,
    session_cost_total_usd: float = 0.0,
) -> TelegramAgentReply:
    """Append one user message to a Telegram agent thread and return the latest AI reply."""

    logger.info("[LEARN] Telegram message entered the LangGraph agent.")
    logger.debug(
        "Running Telegram agent message thread_id=%s input_chars=%s",
        thread_id,
        len(text),
    )
    result = app.invoke(
        {"messages": [HumanMessage(content=text)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    messages = result.get("messages", []) if isinstance(result, dict) else []
    logger.debug("Telegram agent returned %s message(s)", len(messages))
    turn_cost_usd = _log_usage(
        messages,
        purpose="telegram-chat",
        session_cost_total_usd=session_cost_total_usd,
    )
    latest_ai_message = _latest_ai_message(messages)
    if latest_ai_message is None:
        logger.warning("Telegram agent returned no AI reply for thread_id=%s", thread_id)
        return TelegramAgentReply(text="I could not produce a reply. Check logs.")

    text = _message_text(latest_ai_message)
    truncated = _message_was_truncated(latest_ai_message)
    finish_reason = _message_finish_reason(latest_ai_message)
    logger.debug(
        "Telegram agent reply finish_reason=%s truncated=%s output_chars=%s",
        finish_reason or "unknown",
        truncated,
        len(text),
    )
    if truncated:
        # Surface token-limit truncation instead of silently cutting off the reply.
        logger.warning(
            "Telegram agent reply truncated because the model hit the output token limit."
        )
        text = "\n\n".join(
            [text, "Note: reply was cut off because the model hit the output token limit."]
        )
    logger.info("[LEARN] Assistant produced the final Telegram reply.")
    return TelegramAgentReply(text=text, truncated=truncated, usage_cost_usd=turn_cost_usd)


def _build_backlog_tools(settings: AppSettings):
    path = Path(settings.backlog_path)
    if not path.is_absolute():
        path = Path(settings.project_root) / path
    if path.suffix.lower() == ".sqlite3":
        repository: MarkdownBacklogRepository | SqliteBacklogRepository = SqliteBacklogRepository(path)
    else:
        repository = MarkdownBacklogRepository(path)
    project_root = Path(settings.project_root).resolve()
    allowed_roots = [str(project_root / d) for d in settings.allowed_directories]

    @tool
    def count_backlog_items() -> str:
        """Return the number of backlog items."""

        logger.info("[LEARN] Backlog tool is counting backlog items.")
        items = repository.list_open_items()
        if not items:
            return "No open backlog items."
        item_word = "item" if len(items) == 1 else "items"
        return f"Backlog has {len(items)} open {item_word}."

    @tool
    def list_backlog_items(limit: int = 10) -> str:
        """List backlog items sorted by priority. Use this for backlog overview questions."""

        logger.info("[LEARN] Backlog tool is listing backlog items.")
        safe_limit = min(max(limit, 1), 20)
        items = repository.list_open_items_sorted()[:safe_limit]
        if not items:
            return "No open backlog items."
        lines = [format_backlog_list_item(item) for item in items]
        return "\n".join(lines)

    @tool
    def read_backlog_item(item_id: str) -> str:
        """Read one backlog item by ID, such as ATL-001."""

        logger.info("[LEARN] Backlog tool is reading one backlog item.")
        item = repository.get_item(item_id)
        body = _truncate_text(_backlog_body_without_status(item.body), 1600)
        lines = [f"{item.item_id} - {item.title}", f"Status: {item.status.value}"]
        if body:
            lines.extend(["", body])
        return "\n".join(lines)

    @tool
    def set_backlog_item_status(item_id: str, new_status: str) -> str:
        """Set the Status field of a backlog item.

        Only call after the user has confirmed the change.
        Common values: Backlog, Not Done, In Progress, Needs Review, Blocked, Done, Won't Do, Obsolete.
        """

        logger.info(
            "[LEARN] Backlog tool is updating status of %s to '%s'.",
            item_id,
            new_status,
        )
        try:
            item = repository.update_item_status(item_id, new_status)
            return f"Updated {item.item_id} - {item.title}: Status is now '{item.status.value}'."
        except (ValueError, FileNotFoundError) as error:
            return f"Failed to update status: {error}"

    @tool
    def read_project_file(path: str) -> str:
        """Read a file from the project. Use for docs, source code, architecture, or config files.

        path must be relative to the project root and inside an allowed directory.
        Examples: 'docs/ARCHITECTURE.md', 'docs/GRAPH_WORKFLOW.md', 'src/ai_tech_lead/config.py'
        """

        logger.info("[LEARN] File read tool: reading %s", path)
        target = (project_root / path).resolve()
        try:
            target.relative_to(project_root)
        except ValueError:
            return f"Access denied: {path} is outside the project root."
        if not any(str(target).startswith(allowed) for allowed in allowed_roots):
            return f"Access denied: {path} is not in an allowed directory."
        if not target.exists():
            return f"File not found: {path}"
        if not target.is_file():
            return f"Not a file: {path}"
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as err:
            return f"Could not read {path}: {err}"
        return _truncate_text(content, 4000)

    return [count_backlog_items, list_backlog_items, read_backlog_item, set_backlog_item_status, read_project_file]


def _backlog_body_without_status(body: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].strip().lower().startswith("status:"):
        return "\n".join(lines[1:]).strip()
    return body.strip()


def _latest_ai_message(messages: list[Any]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def _message_was_truncated(message: AIMessage) -> bool:
    return _message_finish_reason(message) in {"length", "max_tokens"}


def _message_finish_reason(message: AIMessage) -> str | None:
    metadata = message.response_metadata
    if not isinstance(metadata, dict):
        return None

    finish_reason = metadata.get("finish_reason")
    if isinstance(finish_reason, str):
        return finish_reason
    return None


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def _message_model_name(message: AIMessage) -> str | None:
    metadata = message.response_metadata
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("model_name")
    return name if isinstance(name, str) else None


def _load_pricing() -> dict[str, tuple[float, float]]:
    """Load model pricing from config/llm_pricing.json, cached after first read."""
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache
    try:
        data = json.loads(_LLM_PRICING_PATH.read_text(encoding="utf-8"))
        models = data.get("models", {})
        _pricing_cache = {
            name: (float(v["input"]), float(v["output"]))
            for name, v in models.items()
            if isinstance(v, dict)
        }
    except Exception:
        logger.warning(
            "Could not load LLM pricing from %s — cost reporting disabled", _LLM_PRICING_PATH
        )
        _pricing_cache = {}
    return _pricing_cache


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return approximate USD cost or None if model pricing is unknown."""
    pricing = _load_pricing()
    for key, (in_price, out_price) in pricing.items():
        if model.startswith(key):
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return None


def _extract_token_counts(msg: AIMessage) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from an AIMessage.

    Tries usage_metadata (LangChain standard) first, then falls back to
    response_metadata["token_usage"] (OpenAI Chat Completions API format).
    """
    usage = getattr(msg, "usage_metadata", None)
    if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    token_usage = {}
    if isinstance(getattr(msg, "response_metadata", None), dict):
        token_usage = msg.response_metadata.get("token_usage", {})
    if isinstance(token_usage, dict):
        return token_usage.get("prompt_tokens", 0), token_usage.get("completion_tokens", 0)
    return 0, 0


def _log_usage(
    messages: list[Any],
    *,
    purpose: str,
    session_cost_total_usd: float = 0.0,
) -> float | None:
    """Log total token usage and approximate cost across all LLM calls in a completed turn."""
    input_tokens = 0
    output_tokens = 0
    model: str | None = None
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        if model is None:
            model = _message_model_name(msg)
        tok_in, tok_out = _extract_token_counts(msg)
        input_tokens += tok_in
        output_tokens += tok_out
    if input_tokens == 0 and output_tokens == 0:
        return None
    total_tokens = input_tokens + output_tokens
    cost = _estimate_cost(model or "", input_tokens, output_tokens)
    cost_str = f"~${cost:.4f}" if cost is not None else "unknown"
    session_total = session_cost_total_usd + cost if cost is not None else None
    session_total_str = f"~${session_total:.4f}" if session_total is not None else "unknown"
    logger.info(
        "[LLM] %s  tokens_total=%s %s  (%s in + %s out tokens)  session: %s",
        purpose,
        total_tokens,
        cost_str,
        input_tokens,
        output_tokens,
        session_total_str,
    )
    return cost


def _truncate_text(text: str, limit: int) -> str:
    normalized_text = text.strip()
    if len(normalized_text) <= limit:
        return normalized_text
    return normalized_text[: limit - 20].rstrip() + "\n... [truncated]"
