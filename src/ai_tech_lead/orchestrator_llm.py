"""Small OpenAI Responses API client for orchestrator decisions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_tech_lead.env_loader import load_local_env

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


@dataclass(frozen=True)
class OrchestratorLlmConfig:
    model: str
    max_output_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class OrchestratorLlmResult:
    text: str


class OrchestratorLlmError(RuntimeError):
    """Raised when the orchestrator LLM cannot return a usable response."""


def call_orchestrator_llm(*, prompt: str, config: OrchestratorLlmConfig) -> OrchestratorLlmResult:
    """Call OpenAI using a local project API key from the environment."""

    load_local_env()
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if not api_key or api_key == "replace-with-your-project-service-account-key":
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
        raise OrchestratorLlmError(f"OpenAI API error: {error_body}") from error
    except URLError as error:
        raise OrchestratorLlmError(f"OpenAI API request failed: {error.reason}") from error

    data = json.loads(body)
    text = _extract_response_text(data)
    if not text:
        raise OrchestratorLlmError("OpenAI response did not contain output text.")
    return OrchestratorLlmResult(text=text)


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
