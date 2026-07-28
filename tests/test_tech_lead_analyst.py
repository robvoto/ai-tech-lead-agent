from __future__ import annotations

from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.orchestrator_llm import OrchestratorLlmResult
from ai_tech_lead.tech_lead_analyst import analyse_task


def _settings():
    return replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)


def test_analyse_task_returns_request_unchanged_when_ai_disabled() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=False)

    analysis = analyse_task("Build the feature", [], "", [], settings)

    assert analysis.task_statement == "Build the feature"
    assert analysis.tech_direction == ""


def test_analyse_task_accepts_markdown_fenced_json(monkeypatch) -> None:
    """The live failure this guards against: gpt-4.1-mini sometimes wraps its
    analysis reply in a ```json fence, which used to fall straight back to
    the raw request with an empty tech_direction instead of being parsed."""
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(
            text='```json\n{"task_statement": "Refined task", "tech_direction": "Use the existing service."}\n```'
        ),
    )

    analysis = analyse_task("Build the feature", [], "", [], _settings())

    assert analysis.task_statement == "Refined task"
    assert analysis.tech_direction == "Use the existing service."


def test_analyse_task_retries_once_on_invalid_response_then_succeeds(monkeypatch) -> None:
    calls: list[int] = []

    def fake_call(**_kw):
        calls.append(1)
        if len(calls) == 1:
            return OrchestratorLlmResult(text="")
        return OrchestratorLlmResult(
            text='{"task_statement": "Refined task", "tech_direction": ""}'
        )

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    analysis = analyse_task("Build the feature", [], "", [], _settings())

    assert len(calls) == 2
    assert analysis.task_statement == "Refined task"


def test_analyse_task_falls_back_to_raw_request_after_retry_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(text="not json"),
    )

    analysis = analyse_task("Build the feature", [], "", [], _settings())

    assert analysis.task_statement == "Build the feature"
    assert analysis.tech_direction == ""
