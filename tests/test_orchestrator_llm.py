from __future__ import annotations

import json

from ai_tech_lead.orchestrator_llm import OrchestratorLlmConfig, call_orchestrator_llm


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
        return _FakeResponse(json.dumps({"output_text": "done"}))

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
