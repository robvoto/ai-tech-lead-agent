from __future__ import annotations

import json

import pytest

from ai_tech_lead.orchestrator_llm import (
    OrchestratorLlmConfig,
    OrchestratorLlmError,
    OrchestratorLlmIncompleteError,
    call_orchestrator_llm,
    call_orchestrator_web_search,
)


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def test_call_orchestrator_llm_logs_elapsed_time(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_urlopen(request, timeout):
        return _FakeResponse(
            json.dumps(
                {
                    "output_text": "done",
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                }
            )
        )

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    caplog.set_level("INFO")
    result = call_orchestrator_llm(
        prompt="hello",
        config=OrchestratorLlmConfig(model="test-model", max_output_tokens=10, timeout_seconds=5),
    )

    assert result.text == "done"
    assert "LLM call elapsed:" in caplog.text
    assert "model=test-model" in caplog.text
    assert "status=ok" in caplog.text
    assert "in=10" in caplog.text
    assert "out=4" in caplog.text
    assert "total=14" in caplog.text
    assert "cost_total=$0.00000" in caplog.text


def test_call_orchestrator_llm_sends_reasoning_effort_when_set(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen_payloads: list[dict] = []

    def fake_urlopen(request, timeout):
        seen_payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(
            json.dumps({"output_text": "done", "usage": {"input_tokens": 1, "output_tokens": 1}})
        )

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    call_orchestrator_llm(
        prompt="hello",
        config=OrchestratorLlmConfig(
            model="gpt-5.6-luna", max_output_tokens=10, timeout_seconds=5, reasoning_effort="high"
        ),
    )

    assert seen_payloads[0]["reasoning"] == {"effort": "high"}


def test_call_orchestrator_llm_omits_reasoning_key_when_not_set(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen_payloads: list[dict] = []

    def fake_urlopen(request, timeout):
        seen_payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(
            json.dumps({"output_text": "done", "usage": {"input_tokens": 1, "output_tokens": 1}})
        )

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    call_orchestrator_llm(
        prompt="hello",
        config=OrchestratorLlmConfig(model="gpt-4.1-mini", max_output_tokens=10, timeout_seconds=5),
    )

    assert "reasoning" not in seen_payloads[0]


def test_call_orchestrator_llm_uses_registry_pricing_for_cost(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_urlopen(request, timeout):
        return _FakeResponse(
            json.dumps(
                {
                    "output_text": "done",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
                }
            )
        )

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)
    caplog.set_level("INFO")

    call_orchestrator_llm(
        prompt="hello",
        config=OrchestratorLlmConfig(model="gpt-4.1-mini", max_output_tokens=10, timeout_seconds=5),
    )

    assert "cost_total=$2.00000" in caplog.text


def test_call_orchestrator_web_search_happy_path(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen_payloads: list[dict] = []

    def fake_urlopen(request, timeout):
        seen_payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(
            json.dumps(
                {
                    "output_text": "Found it.",
                    "output": [
                        {
                            "content": [
                                {
                                    "annotations": [
                                        {
                                            "type": "url_citation",
                                            "url": "https://core.telegram.org/bots/api",
                                            "title": "Telegram Bot API",
                                        }
                                    ]
                                }
                            ]
                        }
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 6},
                }
            )
        )

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    caplog.set_level("INFO")
    result = call_orchestrator_web_search(
        query="official Telegram Bot API retry rules",
        config=OrchestratorLlmConfig(model="test-model", max_output_tokens=10, timeout_seconds=5),
    )

    assert seen_payloads[0]["tools"] == [{"type": "web_search"}]
    assert result.text == "Found it."
    assert result.data["output"][0]["content"][0]["annotations"][0]["url"] == (
        "https://core.telegram.org/bots/api"
    )
    assert "Web search call elapsed:" in caplog.text
    assert "status=ok" in caplog.text


def test_call_orchestrator_web_search_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(OrchestratorLlmError):
        call_orchestrator_web_search(
            query="official docs",
            config=OrchestratorLlmConfig(model="test-model", max_output_tokens=10, timeout_seconds=5),
        )


def test_call_orchestrator_web_search_malformed_json_raises(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_urlopen(request, timeout):
        return _FakeResponse("not json")

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    with pytest.raises(OrchestratorLlmError):
        call_orchestrator_web_search(
            query="official docs",
            config=OrchestratorLlmConfig(model="test-model", max_output_tokens=10, timeout_seconds=5),
        )


def test_call_orchestrator_llm_sends_strict_json_schema(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen_payloads: list[dict] = []
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    def fake_urlopen(request, timeout):
        seen_payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(json.dumps({"status": "completed", "output_text": '{"ok":true}'}))

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    result = call_orchestrator_llm(
        prompt="hello",
        config=OrchestratorLlmConfig(model="test-model", max_output_tokens=10, timeout_seconds=5),
        json_schema_name="test_response",
        json_schema=schema,
    )

    assert result.text == '{"ok":true}'
    assert seen_payloads[0]["text"]["format"] == {
        "type": "json_schema",
        "name": "test_response",
        "schema": schema,
        "strict": True,
    }


def test_call_orchestrator_llm_rejects_incomplete_response(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_urlopen(request, timeout):
        return _FakeResponse(
            json.dumps(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output_text": '{"ok":',
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                }
            )
        )

    monkeypatch.setattr("ai_tech_lead.orchestrator_llm.urlopen", fake_urlopen)

    with pytest.raises(OrchestratorLlmIncompleteError, match="max_output_tokens"):
        call_orchestrator_llm(
            prompt="hello",
            config=OrchestratorLlmConfig(model="test-model", max_output_tokens=10, timeout_seconds=5),
        )
