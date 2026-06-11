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
from langgraph.graph import MessagesState, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.backlog_repository import MarkdownBacklogRepository
from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.env_loader import load_local_env
from ai_tech_lead.prompt_loader import load_prompt


logger = logging.getLogger(__name__)

_LLM_PRICING_PATH = PROJECT_ROOT / "config" / "llm_pricing.json"
# Cached after first load — pricing file is read once per process.
_pricing_cache: dict[str, tuple[float, float]] | None = None

SYSTEM_PROMPT = load_prompt("telegram_agent_system.md")


@dataclass(frozen=True)
class TelegramAgentReply:
    """Final reply produced by the Telegram agent graph."""

    text: str
    truncated: bool = False


def build_telegram_agent_graph(*, settings: AppSettings, checkpointer: Any):
    """Build a read-only Telegram agent graph using backlog tools."""

    load_local_env()
    tools = _build_backlog_tools(settings)
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
    system_message = SystemMessage(content=SYSTEM_PROMPT)

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


def run_telegram_agent_message(*, app: Any, thread_id: str, text: str) -> TelegramAgentReply:
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
    _log_usage(messages, purpose="telegram-chat")
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
        logger.warning("Telegram agent reply truncated because the model hit the output token limit.")
        text = "\n\n".join([text, "Note: reply was cut off because the model hit the output token limit."])
    logger.info("[LEARN] Assistant produced the final Telegram reply.")
    return TelegramAgentReply(text=text, truncated=truncated)


def _build_backlog_tools(settings: AppSettings):
    repository = MarkdownBacklogRepository(Path(settings.backlog_path))

    @tool
    def count_backlog_items() -> str:
        """Return the number of backlog items."""

        logger.info("[LEARN] Backlog tool is counting backlog items.")
        items = repository.list_items()
        return f"Backlog has {len(items)} items."

    @tool
    def list_backlog_items(limit: int = 10) -> str:
        """List backlog item IDs and titles. Use this for backlog overview questions."""

        logger.info("[LEARN] Backlog tool is listing backlog items.")
        safe_limit = min(max(limit, 1), 20)
        items = repository.list_items()[:safe_limit]
        lines = [f"{item.item_id} - {item.title}" for item in items]
        return "\n".join(lines)

    @tool
    def read_backlog_item(item_id: str) -> str:
        """Read one backlog item by ID, such as ATL-001."""

        logger.info("[LEARN] Backlog tool is reading one backlog item.")
        item = repository.get_item(item_id)
        body = _truncate_text(item.body, 1600)
        return f"{item.item_id} - {item.title}\n\n{body}"

    @tool
    def set_backlog_item_status(item_id: str, new_status: str) -> str:
        """Set the Status field of a backlog item. Only call after the user has confirmed the change.
        Common values: Backlog, In Progress, Done, Blocked, Cancelled."""

        logger.info(
            "[LEARN] Backlog tool is updating status of %s to '%s'.",
            item_id,
            new_status,
        )
        try:
            item = repository.update_item_status(item_id, new_status)
            return f"Updated {item.item_id} - {item.title}: Status is now '{new_status}'."
        except (ValueError, FileNotFoundError) as error:
            return f"Failed to update status: {error}"

    return [count_backlog_items, list_backlog_items, read_backlog_item, set_backlog_item_status]


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
        logger.warning("Could not load LLM pricing from %s — cost reporting disabled", _LLM_PRICING_PATH)
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


def _log_usage(messages: list[Any], *, purpose: str) -> None:
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
        return
    cost = _estimate_cost(model or "", input_tokens, output_tokens)
    cost_str = f"~${cost:.4f}" if cost is not None else "unknown"
    logger.info("[LLM] %s  %s  (%s in + %s out tokens)", purpose, cost_str, input_tokens, output_tokens)


def _truncate_text(text: str, limit: int) -> str:
    normalized_text = text.strip()
    if len(normalized_text) <= limit:
        return normalized_text
    return normalized_text[: limit - 20].rstrip() + "\n... [truncated]"
