"""Small OpenAI Responses API client for orchestrator decisions."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_tech_lead.env_loader import load_local_env
from ai_tech_lead.logging_setup import LOGGER_NAME
from ai_tech_lead.model_registry import estimate_cost as _registry_estimate_cost
from ai_tech_lead.run_budget import (
    begin_active_run_budget_call,
    record_active_run_budget_usage,
)

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class OrchestratorLlmConfig:
    model: str
    max_output_tokens: int
    timeout_seconds: int
    reasoning_effort: str | None = None
    purpose: str = ""
    profile_name: str = ""


@dataclass(frozen=True)
class OrchestratorLlmResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class OrchestratorWebSearchResult:
    """Raw parsed response from a web-search-enabled orchestrator call."""

    data: dict
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class OrchestratorLlmError(RuntimeError):
    """Raised when the orchestrator LLM cannot return a usable response."""


class OrchestratorLlmIncompleteError(OrchestratorLlmError):
    """Raised when the Responses API stops before producing a complete response."""


def call_orchestrator_llm(
    *,
    prompt: str,
    config: OrchestratorLlmConfig,
    json_schema_name: str | None = None,
    json_schema: dict | None = None,
) -> OrchestratorLlmResult:
    """Call OpenAI using a local project API key from the environment."""

    load_local_env()
    start_time = time.perf_counter()
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key or api_key == "replace-with-your-project-service-account-key":
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "LLM call elapsed: %.0fms model=%s status=skipped "
            "in=0 out=0 total=0 cost_total=$0.00000",
            elapsed_ms,
            config.model,
        )
        raise OrchestratorLlmError("OPENAI_API_KEY is not configured.")

    payload: dict = {
        "model": config.model,
        "input": prompt,
        "max_output_tokens": config.max_output_tokens,
    }
    if config.reasoning_effort is not None:
        payload["reasoning"] = {"effort": config.reasoning_effort}
    if (json_schema_name is None) != (json_schema is None):
        raise ValueError("json_schema_name and json_schema must be supplied together")
    if json_schema is not None:
        # `text.format` is the protocol-owned Responses API boundary for strict
        # structured output; each caller owns its task-specific schema.
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": json_schema_name,
                "schema": json_schema,
                "strict": True,
            }
        }
    request = Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    begin_active_run_budget_call()
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "LLM call elapsed: %.0fms model=%s status=error "
            "in=0 out=0 total=0 cost_total=$0.00000",
            elapsed_ms,
            config.model,
        )
        raise OrchestratorLlmError(f"OpenAI API error: {error_body}") from error
    except URLError as error:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "LLM call elapsed: %.0fms model=%s status=error "
            "in=0 out=0 total=0 cost_total=$0.00000",
            elapsed_ms,
            config.model,
        )
        raise OrchestratorLlmError(f"OpenAI API request failed: {error.reason}") from error

    data = json.loads(body)
    tokens_in, tokens_out, cost_usd = _response_usage(data, config.model)
    record_active_run_budget_usage(
        tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd
    )
    if data.get("status") == "incomplete":
        details = data.get("incomplete_details")
        reason = (
            str(details.get("reason", "unknown")).strip()
            if isinstance(details, dict)
            else "unknown"
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "LLM call elapsed: %.0fms model=%s purpose=%s profile=%s effort=%s "
            "status=incomplete reason=%s in=%d out=%d total=%d cost_total=$%.5f",
            elapsed_ms,
            config.model,
            config.purpose or "-",
            config.profile_name or "-",
            config.reasoning_effort or "-",
            reason,
            tokens_in,
            tokens_out,
            tokens_in + tokens_out,
            cost_usd,
        )
        raise OrchestratorLlmIncompleteError(f"OpenAI response incomplete: {reason}")
    text = _extract_response_text(data)
    if not text:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "LLM call elapsed: %.0fms model=%s status=error "
            "in=0 out=0 total=0 cost_total=$0.00000",
            elapsed_ms,
            config.model,
        )
        raise OrchestratorLlmError("OpenAI response did not contain output text.")

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    tokens_total = tokens_in + tokens_out
    logger.info(
        "LLM call elapsed: %.0fms model=%s purpose=%s profile=%s effort=%s "
        "status=ok in=%d out=%d total=%d cost_total=$%.5f",
        elapsed_ms,
        config.model,
        config.purpose or "-",
        config.profile_name or "-",
        config.reasoning_effort or "-",
        tokens_in,
        tokens_out,
        tokens_total,
        cost_usd,
    )
    return OrchestratorLlmResult(
        text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd
    )


def call_orchestrator_web_search(
    *, query: str, config: OrchestratorLlmConfig
) -> OrchestratorWebSearchResult:
    """Call OpenAI's Responses API with the hosted web_search tool enabled.

    Kept as a separate function from call_orchestrator_llm so tool-use
    capability is isolated to this one call site — every other orchestrator
    LLM call (risk review, plan review, tech-lead analysis, the knowledge-gap
    check) stays a plain text-completion request with no tools.
    """

    load_local_env()
    start_time = time.perf_counter()
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key or api_key == "replace-with-your-project-service-account-key":
        logger.info("Web search call elapsed: 0ms model=%s status=skipped", config.model)
        raise OrchestratorLlmError("OPENAI_API_KEY is not configured.")

    payload: dict = {
        "model": config.model,
        "input": query,
        "max_output_tokens": config.max_output_tokens,
        "tools": [{"type": "web_search"}],
    }
    if config.reasoning_effort is not None:
        payload["reasoning"] = {"effort": config.reasoning_effort}
    request = Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    begin_active_run_budget_call()
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        logger.info("Web search call elapsed: model=%s status=error", config.model)
        raise OrchestratorLlmError(f"OpenAI web search API error: {error_body}") from error
    except URLError as error:
        logger.info("Web search call elapsed: model=%s status=error", config.model)
        raise OrchestratorLlmError(f"OpenAI web search API request failed: {error.reason}") from error

    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        raise OrchestratorLlmError(f"OpenAI web search response was not valid JSON: {error}") from error

    text = _extract_response_text(data)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    tokens_in, tokens_out, cost_usd = _response_usage(data, config.model)
    record_active_run_budget_usage(
        tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd
    )
    logger.info(
        "Web search call elapsed: %.0fms model=%s purpose=%s profile=%s effort=%s "
        "status=ok in=%d out=%d cost_total=$%.5f",
        elapsed_ms,
        config.model,
        config.purpose or "-",
        config.profile_name or "-",
        config.reasoning_effort or "-",
        tokens_in,
        tokens_out,
        cost_usd,
    )
    return OrchestratorWebSearchResult(
        data=data, text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd
    )


def _response_usage(data: dict, model: str) -> tuple[int, int, float]:
    """Return provider-reported usage and the registry-estimated cost."""

    usage = data.get("usage", {})
    tokens_in = int(usage.get("input_tokens", 0))
    tokens_out = int(usage.get("output_tokens", 0))
    return tokens_in, tokens_out, _estimate_cost(model, tokens_in, tokens_out)


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    return _registry_estimate_cost(model, input_tokens=tokens_in, output_tokens=tokens_out).usd


def _extract_response_text(data: dict) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for output_item in data.get("output", []):
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []):
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())

    return "\n".join(chunks).strip()
