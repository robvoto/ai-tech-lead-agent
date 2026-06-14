from __future__ import annotations

import json

from helpers import valid_settings_dict

from ai_tech_lead.risk_reviewer import review_task_risk


def _load_settings_from(path):
    from ai_tech_lead.app_settings import load_settings

    return load_settings(path)


def test_risk_review_falls_back_to_approval_when_ai_disabled(monkeypatch, tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(valid_settings_dict()), encoding="utf-8")
    monkeypatch.setattr(
        "ai_tech_lead.risk_reviewer.load_settings",
        lambda: _load_settings_from(settings_path),
    )

    decision = review_task_risk("Small docs change")

    assert decision.needs_approval is True
    assert "Small docs change" in decision.approval_reason


def test_risk_review_uses_ai_when_enabled(monkeypatch, tmp_path) -> None:
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(raw_settings), encoding="utf-8")
    monkeypatch.setattr(
        "ai_tech_lead.risk_reviewer.load_settings",
        lambda: _load_settings_from(settings_path),
    )

    class FakeResult:
        text = (
            '{"needs_approval": false, "reason": "Small safe task", '
            '"confidence": 0.9}'
        )

    monkeypatch.setattr(
        "ai_tech_lead.risk_reviewer.call_orchestrator_llm",
        lambda prompt, config: FakeResult(),
    )

    decision = review_task_risk("Small docs change")

    assert decision.needs_approval is False
    assert "Small safe task" in decision.approval_reason


def test_risk_review_requires_approval_when_ai_confidence_is_low(monkeypatch, tmp_path) -> None:
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(raw_settings), encoding="utf-8")
    monkeypatch.setattr(
        "ai_tech_lead.risk_reviewer.load_settings",
        lambda: _load_settings_from(settings_path),
    )

    class FakeResult:
        text = (
            '{"needs_approval": false, "reason": "Looks safe", '
            '"confidence": 0.5}'
        )

    monkeypatch.setattr(
        "ai_tech_lead.risk_reviewer.call_orchestrator_llm",
        lambda prompt, config: FakeResult(),
    )

    decision = review_task_risk("Small docs change")

    assert decision.needs_approval is True
    assert "Low confidence" in decision.approval_reason
