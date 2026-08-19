from __future__ import annotations

import json
from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.orchestrator_llm import OrchestratorLlmError, OrchestratorLlmResult
from ai_tech_lead.project_guidance_governance import review_project_guidance


def _ai_enabled_settings():
    return replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)


def _fake_llm(payload: dict) -> None:
    return lambda **_kwargs: OrchestratorLlmResult(text=json.dumps(payload))


def test_sufficient_guidance_does_not_require_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        _fake_llm(
            {
                "status": "sufficient",
                "requires_review": False,
                "summary": "",
                "related_locations": [],
                "proposed_change": "",
                "reason": "AGENTS.md already covers this.",
            }
        ),
    )

    decision = review_project_guidance("Fix a typo", ["AGENTS.md: some rule."], _ai_enabled_settings())

    assert decision.status == "sufficient"
    assert decision.requires_review is False


def test_missing_guidance_that_matters_requires_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        _fake_llm(
            {
                "status": "missing",
                "requires_review": True,
                "summary": "No rule covers how database migrations should be written.",
                "related_locations": [],
                "proposed_change": "Add a bullet under AGENTS.md's Universal rules about migrations.",
                "reason": "The task adds a schema migration and nothing discovered addresses this.",
            }
        ),
    )

    decision = review_project_guidance(
        "Add a database migration for the new column",
        [],
        _ai_enabled_settings(),
    )

    assert decision.status == "missing"
    assert decision.requires_review is True
    assert "migration" in decision.summary
    assert decision.proposed_change


def test_missing_guidance_that_does_not_matter_does_not_block(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        _fake_llm(
            {
                "status": "missing",
                "requires_review": False,
                "summary": "No dedicated typo-fixing rule exists, but none is needed.",
                "related_locations": [],
                "proposed_change": "",
                "reason": "A one-line typo fix does not depend on any project-specific rule.",
            }
        ),
    )

    decision = review_project_guidance("Fix a typo in a comment", [], _ai_enabled_settings())

    assert decision.status == "missing"
    assert decision.requires_review is False


def test_conflicting_guidance_always_requires_review_even_if_model_says_no(monkeypatch) -> None:
    """Conflicting guidance is never silently merged — requires_review is forced
    true regardless of what the model itself reported."""
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        _fake_llm(
            {
                "status": "conflicting",
                "requires_review": False,
                "summary": "AGENTS.md says use pytest; docs/INDEX.md says use unittest.",
                "related_locations": ["AGENTS.md", "docs/INDEX.md"],
                "proposed_change": "Reconcile the two testing-framework statements.",
                "reason": "The task involves writing tests and the guidance disagrees on the framework.",
            }
        ),
    )

    decision = review_project_guidance(
        "Write tests for the new module",
        ["AGENTS.md: use pytest.", "docs/INDEX.md: use unittest."],
        _ai_enabled_settings(),
    )

    assert decision.status == "conflicting"
    assert decision.requires_review is True
    assert decision.related_locations == ["AGENTS.md", "docs/INDEX.md"]


def test_ai_disabled_fails_closed_to_conflicting_and_requires_review() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=False)

    decision = review_project_guidance("Do something", [], settings)

    assert decision.status == "conflicting"
    assert decision.requires_review is True
    assert decision.summary


def test_llm_error_fails_closed_to_conflicting_and_requires_review(monkeypatch) -> None:
    def raise_error(**_kwargs):
        raise OrchestratorLlmError("network unavailable")

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", raise_error)

    decision = review_project_guidance("Do something", [], _ai_enabled_settings())

    assert decision.status == "conflicting"
    assert decision.requires_review is True


def test_invalid_response_fails_closed_to_conflicting_and_requires_review(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kwargs: OrchestratorLlmResult(text="not json"),
    )

    decision = review_project_guidance("Do something", [], _ai_enabled_settings())

    assert decision.status == "conflicting"
    assert decision.requires_review is True


def test_unknown_status_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        _fake_llm(
            {
                "status": "unsure",
                "requires_review": False,
                "summary": "",
                "related_locations": [],
                "proposed_change": "",
                "reason": "",
            }
        ),
    )

    decision = review_project_guidance("Do something", [], _ai_enabled_settings())

    # Invalid status shape fails closed, same as any other invalid response.
    assert decision.status == "conflicting"
    assert decision.requires_review is True
