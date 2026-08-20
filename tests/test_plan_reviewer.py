from __future__ import annotations

from dataclasses import replace

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.orchestrator_llm import OrchestratorLlmResult
from ai_tech_lead.plan_reviewer import PlanReviewUnavailable, review_plan


def test_review_plan_empty_plan_includes_agent_error_in_reason() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    decision = review_plan(
        "Build the feature", "", settings, agent_error="ERROR: usage limit exceeded"
    )

    assert decision.approved is False
    assert "empty plan" in decision.reason.lower()
    assert "usage limit exceeded" in decision.reason


def test_review_plan_empty_plan_without_agent_error() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    decision = review_plan("Build the feature", "", settings)

    assert decision.approved is False
    assert decision.reason == "The coding agent returned an empty plan."


def test_review_plan_rejects_verbose_plan_without_calling_llm(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    plan_text = "\n".join(
        [
            "1. Inspect the request and current implementation.",
            "2. Make the smallest safe code change.",
            "3. Update or add the focused test coverage.",
            "4. Run validation and fix any regressions.",
            "5. Check the result against the request.",
            "6. Document the outcome briefly.",
        ]
    )

    decision = review_plan("Build the feature", plan_text, settings)

    assert decision.approved is False
    assert "too long" in decision.reason.lower()
    assert "3 to 5 short bullets" in decision.correction


def test_review_plan_allows_five_bullets_plus_done_when(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(
            text='{"approved": true, "reason": "Plan shape is valid.", "correction": ""}'
        ),
    )

    plan_text = "\n".join(
        [
            "- Fix the shared parser.",
            "- Fix execution reporting.",
            "- Fix resumed task kind.",
            "- Fix bounded guidance discovery.",
            "- Add focused tests.",
            "Done when: targeted tests pass.",
        ]
    )

    decision = review_plan("Fix the defects", plan_text, settings)

    assert decision.approved is True


def test_review_plan_accepts_markdown_fenced_json(monkeypatch) -> None:
    """The live failure this guards against: gpt-4.1-mini sometimes wraps its
    review reply in a ```json fence, which used to crash with
    'Expecting value: line 1 column 1 (char 0)' instead of being parsed."""
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(
            text='```json\n{"approved": true, "reason": "Looks good."}\n```'
        ),
    )

    decision = review_plan("Build the feature", "1. Do the thing.", settings)

    assert decision.approved is True
    assert decision.reason == "Looks good."


def test_review_plan_retries_once_on_invalid_response_then_succeeds(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)
    calls: list[int] = []

    def fake_call(**_kw):
        calls.append(1)
        if len(calls) == 1:
            return OrchestratorLlmResult(text="")
        return OrchestratorLlmResult(text='{"approved": true, "reason": "Fine on retry."}')

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    decision = review_plan("Build the feature", "1. Do the thing.", settings)

    assert len(calls) == 2
    assert decision.approved is True
    assert decision.reason == "Fine on retry."


def test_review_plan_routes_to_human_after_retry_exhausted(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(text="not json"),
    )

    with pytest.raises(PlanReviewUnavailable, match="invalid response"):
        review_plan("Build the feature", "1. Do the thing.", settings)


def test_project_guidance_is_included_in_the_plan_review_prompt_when_present(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)
    captured: dict = {}

    def fake_call(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return OrchestratorLlmResult(text='{"approved": true, "reason": "Looks good."}')

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    review_plan(
        "Build the feature",
        "1. Do the thing.",
        settings,
        project_guidance=["AGENTS.md: run `make test` before reporting done."],
    )

    assert "Target project's own guidance" in captured["prompt"]
    assert "run `make test` before reporting done" in captured["prompt"]


def test_project_guidance_omitted_from_plan_review_prompt_when_empty(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)
    captured: dict = {}

    def fake_call(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return OrchestratorLlmResult(text='{"approved": true, "reason": "Looks good."}')

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    review_plan("Build the feature", "1. Do the thing.", settings)

    assert "Target project's own guidance" not in captured["prompt"]
