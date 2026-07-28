"""Shared parsing/retry for orchestrator LLM calls that expect a JSON reply.

Every orchestrator-AI call site that expects strict JSON from the model faces
the same two failure modes: the model wraps its answer in a Markdown code
fence, or it returns something that doesn't validate even once unwrapped.
This is the one place that tolerance and retry logic lives, instead of being
copy-pasted (and drifting) per call site.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypeVar

from .logging_setup import LOGGER_NAME
from .orchestrator_llm import OrchestratorLlmConfig, OrchestratorLlmResult, call_orchestrator_llm

logger = logging.getLogger(LOGGER_NAME)

DEFAULT_JSON_CALL_ATTEMPTS = 2

T = TypeVar("T")


def strip_json_fence(text: str) -> str:
    """Return `text` with one enclosing ```...``` Markdown fence removed, if present."""
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3:
        return "\n".join(lines[1:-1]).strip()
    return stripped


def parse_json_object(raw_text: str, *, error_label: str) -> dict[str, Any]:
    """Parse `raw_text` as a JSON object, tolerating one Markdown code fence."""
    payload = json.loads(strip_json_fence(raw_text))
    if not isinstance(payload, dict):
        raise TypeError(f"{error_label} must be a JSON object")
    return payload


def call_llm_for_json(
    *,
    prompt: str,
    config: OrchestratorLlmConfig,
    error_label: str,
    parse: Callable[[dict[str, Any]], T],
    attempts: int = DEFAULT_JSON_CALL_ATTEMPTS,
    on_result: Callable[[OrchestratorLlmResult], None] | None = None,
) -> T:
    """Call the orchestrator LLM and parse+validate its JSON reply, retrying
    on any invalid output — malformed JSON, a Markdown-fenced response, or a
    shape that fails `parse` — before giving up and raising the last error.

    `on_result` runs after each raw call (before parsing) so a caller can log
    its own token/cost metric line with its own wording; the retry/parse
    logic itself stays centralized here.
    """
    last_error: Exception = ValueError(f"{error_label}: produced no attempts")
    for attempt in range(1, attempts + 1):
        result = call_orchestrator_llm(prompt=prompt, config=config)
        if on_result is not None:
            on_result(result)
        try:
            payload = parse_json_object(result.text, error_label=error_label)
            return parse(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            logger.warning(
                "%s invalid on attempt %d/%d: %s — raw response: %r",
                error_label,
                attempt,
                attempts,
                error,
                result.text,
            )

    raise last_error
