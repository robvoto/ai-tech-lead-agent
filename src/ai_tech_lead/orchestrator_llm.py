"""Small OpenAI Responses API client for orchestrator decisions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_tech_lead.env_loader import load_local_env
from ai_tech_lead.logging_setup import LOGGER_NAME

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class OrchestratorLlmConfig:
    model: str
    max_output_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class OrchestratorLlmResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class OrchestratorLlmError(RuntimeError):
    """Raised when the orchestrator LLM cannot return a usable response."""


def call_orchestrator_llm(*, prompt: str, config: OrchestratorLlmConfig) -> OrchestratorLlmResult:
    """Call OpenAI using a local project API key from the environment."""

    load_local_env()
    start_time = time.perf_counter()
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key or api_key == "replace-with-your-project-service-account-key":
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info("LLM call elapsed: %.0fms model=%s status=skipped", elapsed_ms, config.model)
        raise OrchestratorLlmError("OPENAI_API_KEY is not configured.")

    payload = {
        "model": config.model,
        "input": prompt,
        "max_output_tokens": config.max_output_tokens,
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

    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info("LLM call elapsed: %.0fms model=%s status=error", elapsed_ms, config.model)
        raise OrchestratorLlmError(f"OpenAI API error: {error_body}") from error
    except URLError as error:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info("LLM call elapsed: %.0fms model=%s status=error", elapsed_ms, config.model)
        raise OrchestratorLlmError(f"OpenAI API request failed: {error.reason}") from error

    data = json.loads(body)
    text = _extract_response_text(data)
    if not text:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info("LLM call elapsed: %.0fms model=%s status=error", elapsed_ms, config.model)
        raise OrchestratorLlmError("OpenAI response did not contain output text.")

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    usage = data.get("usage", {})
    tokens_in = int(usage.get("input_tokens", 0))
    tokens_out = int(usage.get("output_tokens", 0))
    cost_usd = _estimate_cost(config.model, tokens_in, tokens_out)
    logger.info(
        "LLM call elapsed: %.0fms model=%s status=ok in=%d out=%d cost=$%.5f",
        elapsed_ms,
        config.model,
        tokens_in,
        tokens_out,
        cost_usd,
    )
    return OrchestratorLlmResult(text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)


# Approximate USD per 1K tokens. Update when pricing changes.
_COST_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4.1":      {"input": 0.002,  "output": 0.008},
    "gpt-4o-mini":  {"input": 0.00015, "output": 0.0006},
    "gpt-4o":       {"input": 0.0025,  "output": 0.01},
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    prices = _COST_PER_1K.get(model)
    if prices is None:
        return 0.0
    return (tokens_in / 1000) * prices["input"] + (tokens_out / 1000) * prices["output"]


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
