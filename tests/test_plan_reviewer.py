from __future__ import annotations

from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.plan_reviewer import review_plan


def test_review_plan_rejects_verbose_plan_without_calling_llm(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.plan_reviewer.call_orchestrator_llm",
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
