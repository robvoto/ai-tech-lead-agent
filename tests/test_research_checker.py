from __future__ import annotations

from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.orchestrator_llm import OrchestratorLlmResult
from ai_tech_lead.research_checker import check_research_requirements


def _ai_enabled_settings():
    return replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)


def test_knowledge_gap_check_retries_once_on_malformed_response(monkeypatch, caplog):
    calls = []

    def fake_call_orchestrator_llm(*, prompt, config):
        del prompt, config
        calls.append(1)
        if len(calls) == 1:
            return OrchestratorLlmResult(text="")
        return OrchestratorLlmResult(text='{"has_gap": false, "gap_question": "", "reason": "no external fact needed"}')

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        fake_call_orchestrator_llm,
    )

    with caplog.at_level("WARNING"):
        result = check_research_requirements("do a small thing", _ai_enabled_settings())

    assert len(calls) == 2
    assert result.has_gap is False
    assert result.online_research_needed is False
    assert any("raw response" in message for message in caplog.messages)


def test_knowledge_gap_check_falls_back_to_approval_after_retry_exhausted(monkeypatch):
    calls = []

    def fake_call_orchestrator_llm(*, prompt, config):
        del prompt, config
        calls.append(1)
        return OrchestratorLlmResult(text="not json")

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        fake_call_orchestrator_llm,
    )

    result = check_research_requirements("do a big thing", _ai_enabled_settings())

    assert len(calls) == 2
    assert result.has_gap is True
    assert result.online_research_needed is True
    assert result.gap_reason == "Knowledge-gap check response invalid; requiring human approval."


def test_knowledge_gap_check_accepts_markdown_fenced_json(monkeypatch):
    def fake_call_orchestrator_llm(*, prompt, config):
        del prompt, config
        return OrchestratorLlmResult(
            text='```json\n{"has_gap": false, "gap_question": "", "reason": "planning only"}\n```'
        )

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        fake_call_orchestrator_llm,
    )

    result = check_research_requirements("explain a plan", _ai_enabled_settings())

    assert result.has_gap is False
    assert result.online_research_needed is False


def test_knowledge_gap_check_rejects_missing_gap_question_when_gap_true(monkeypatch):
    def fake_call_orchestrator_llm(*, prompt, config):
        del prompt, config
        return OrchestratorLlmResult(text='{"has_gap": true, "gap_question": "", "reason": "unclear"}')

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        fake_call_orchestrator_llm,
    )

    result = check_research_requirements("do something", _ai_enabled_settings())

    assert result.has_gap is True
    assert result.online_research_needed is True
    assert result.gap_reason == "Knowledge-gap check response invalid; requiring human approval."
