from __future__ import annotations

from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.operator_question import answer_operator_question
from ai_tech_lead.orchestrator_llm import OrchestratorLlmError, OrchestratorLlmResult
from ai_tech_lead.target_project_context import TargetProjectContext


def _settings(**overrides):
    fields = {"orchestrator_ai_enabled": True}
    fields.update(overrides)
    return replace(parse_settings(valid_settings_dict()), **fields)


def test_answer_operator_question_ai_disabled_returns_fallback(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("LLM should not be called when AI is disabled")

    monkeypatch.setattr(
        "ai_tech_lead.operator_question.call_orchestrator_llm", fail_if_called
    )

    answer = answer_operator_question(
        question="Which files will this touch?",
        request="Update the runtime docs.",
        target_project_context=None,
        code_context="(none)",
        research_evidence=[],
        settings=_settings(orchestrator_ai_enabled=False),
    )

    assert "AI is disabled" in answer
    assert "approve" in answer.lower()


def test_answer_operator_question_llm_error_returns_fallback(monkeypatch) -> None:
    def fake_call_orchestrator_llm(*, prompt, config):
        del prompt, config
        raise OrchestratorLlmError("timed out")

    monkeypatch.setattr(
        "ai_tech_lead.operator_question.call_orchestrator_llm", fake_call_orchestrator_llm
    )

    answer = answer_operator_question(
        question="Which files will this touch?",
        request="Update the runtime docs.",
        target_project_context=None,
        code_context="(none)",
        research_evidence=[],
        settings=_settings(),
    )

    assert "couldn't answer" in answer.lower()


def test_answer_operator_question_returns_llm_text(monkeypatch) -> None:
    seen_prompts: list[str] = []

    def fake_call_orchestrator_llm(*, prompt, config):
        del config
        seen_prompts.append(prompt)
        return OrchestratorLlmResult(text="Only docs/RUNTIME_RUNBOOK.md will change.")

    monkeypatch.setattr(
        "ai_tech_lead.operator_question.call_orchestrator_llm", fake_call_orchestrator_llm
    )

    answer = answer_operator_question(
        question="Which files will this touch?",
        request="Update the runtime docs.",
        target_project_context=TargetProjectContext(project_name="AI Tech Lead"),
        code_context="- docs/RUNTIME_RUNBOOK.md: runbook contents",
        research_evidence=["LangGraph interrupts | https://... | Interrupts pause execution."],
        settings=_settings(),
    )

    assert answer == "Only docs/RUNTIME_RUNBOOK.md will change."
    assert "Which files will this touch?" in seen_prompts[0]
    assert "Update the runtime docs." in seen_prompts[0]
    assert "AI Tech Lead" in seen_prompts[0]
    assert "runbook contents" in seen_prompts[0]
    assert "Interrupts pause execution." in seen_prompts[0]


def test_answer_operator_question_empty_llm_response_returns_fallback(monkeypatch) -> None:
    def fake_call_orchestrator_llm(*, prompt, config):
        del prompt, config
        return OrchestratorLlmResult(text="   ")

    monkeypatch.setattr(
        "ai_tech_lead.operator_question.call_orchestrator_llm", fake_call_orchestrator_llm
    )

    answer = answer_operator_question(
        question="Which files will this touch?",
        request="Update the runtime docs.",
        target_project_context=None,
        code_context="(none)",
        research_evidence=[],
        settings=_settings(),
    )

    assert "couldn't answer" in answer.lower()
