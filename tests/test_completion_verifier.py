from __future__ import annotations

import json
from dataclasses import replace

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.completion_verifier import (
    CompletionVerificationUnavailable,
    verify_completion,
)
from ai_tech_lead.orchestrator_llm import OrchestratorLlmError, OrchestratorLlmResult


def _ai_enabled_settings():
    return replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)


def _call(**overrides):
    kwargs = {
        "bounded_request": "Add a logout button",
        "formulated_task": "Add a logout button to the settings page",
        "brief": "Small UI change",
        "plan_text": "1. Add button\n2. Wire handler\nDone when logout works",
        "acceptance_criteria": ["Logout button is visible", "Clicking it signs the user out"],
        "changed_files": ("src/app/settings.py",),
        "coding_agent_result": "Ran pytest, all green. Changed files: settings.py",
        "settings": _ai_enabled_settings(),
        "prior_correction": "",
    }
    kwargs.update(overrides)
    return verify_completion(**kwargs)


def test_verify_completion_raises_when_ai_disabled() -> None:
    with pytest.raises(CompletionVerificationUnavailable):
        _call(settings=parse_settings(valid_settings_dict()))


def test_verify_completion_returns_complete(monkeypatch) -> None:
    result = OrchestratorLlmResult(
        text=json.dumps(
            {
                "status": "complete",
                "reason": "Acceptance criteria met and tests pass.",
                "correction": "",
            }
        ),
        tokens_in=100,
        tokens_out=20,
        cost_usd=0.001,
    )
    monkeypatch.setattr(
        "ai_tech_lead.completion_verifier.call_orchestrator_llm", lambda **_kw: result
    )

    decision = _call()

    assert decision.status == "complete"
    assert "Acceptance criteria met" in decision.reason


def test_verify_completion_returns_correction_required(monkeypatch) -> None:
    result = OrchestratorLlmResult(
        text=json.dumps(
            {
                "status": "correction_required",
                "reason": "The logout button does not sign the user out.",
                "correction": "Wire the button's click handler to the sign-out endpoint.",
            }
        ),
        tokens_in=100,
        tokens_out=20,
        cost_usd=0.001,
    )
    monkeypatch.setattr(
        "ai_tech_lead.completion_verifier.call_orchestrator_llm", lambda **_kw: result
    )

    decision = _call()

    assert decision.status == "correction_required"
    assert "sign-out endpoint" in decision.correction


def test_verify_completion_returns_human_verification_required(monkeypatch) -> None:
    result = OrchestratorLlmResult(
        text=json.dumps(
            {
                "status": "human_verification_required",
                "reason": "Requires a visual check of the settings page layout.",
                "correction": "",
            }
        ),
        tokens_in=100,
        tokens_out=20,
        cost_usd=0.001,
    )
    monkeypatch.setattr(
        "ai_tech_lead.completion_verifier.call_orchestrator_llm", lambda **_kw: result
    )

    decision = _call()

    assert decision.status == "human_verification_required"


def test_verify_completion_raises_when_llm_call_fails(monkeypatch) -> None:
    def _raise(**_kw):
        raise OrchestratorLlmError("timeout")

    monkeypatch.setattr("ai_tech_lead.completion_verifier.call_orchestrator_llm", _raise)

    with pytest.raises(CompletionVerificationUnavailable):
        _call()


def test_verify_completion_raises_on_malformed_json(monkeypatch) -> None:
    result = OrchestratorLlmResult(text="not json", tokens_in=1, tokens_out=1, cost_usd=0.0)
    monkeypatch.setattr(
        "ai_tech_lead.completion_verifier.call_orchestrator_llm", lambda **_kw: result
    )

    with pytest.raises(CompletionVerificationUnavailable):
        _call()


def test_verify_completion_raises_on_invalid_status(monkeypatch) -> None:
    result = OrchestratorLlmResult(
        text=json.dumps({"status": "done", "reason": "looks fine", "correction": ""}),
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
    )
    monkeypatch.setattr(
        "ai_tech_lead.completion_verifier.call_orchestrator_llm", lambda **_kw: result
    )

    with pytest.raises(CompletionVerificationUnavailable):
        _call()


def test_project_guidance_is_included_in_the_completion_verification_prompt(monkeypatch) -> None:
    captured: dict = {}

    def fake_call(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return OrchestratorLlmResult(
            text=json.dumps({"status": "complete", "reason": "Looks good.", "correction": ""}),
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
        )

    monkeypatch.setattr("ai_tech_lead.completion_verifier.call_orchestrator_llm", fake_call)

    _call(project_guidance=["AGENTS.md: run `make test` before reporting done."])

    assert "Target project's own guidance" in captured["prompt"]
    assert "run `make test` before reporting done" in captured["prompt"]


def test_project_guidance_omitted_from_completion_verification_prompt_when_empty(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_call(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return OrchestratorLlmResult(
            text=json.dumps({"status": "complete", "reason": "Looks good.", "correction": ""}),
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
        )

    monkeypatch.setattr("ai_tech_lead.completion_verifier.call_orchestrator_llm", fake_call)

    _call()

    assert "Target project's own guidance" not in captured["prompt"]


def test_verify_completion_raises_when_correction_required_has_empty_correction(monkeypatch) -> None:
    result = OrchestratorLlmResult(
        text=json.dumps(
            {"status": "correction_required", "reason": "Something is missing.", "correction": ""}
        ),
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
    )
    monkeypatch.setattr(
        "ai_tech_lead.completion_verifier.call_orchestrator_llm", lambda **_kw: result
    )

    with pytest.raises(CompletionVerificationUnavailable):
        _call()
