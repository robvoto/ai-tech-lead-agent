from __future__ import annotations

import json
from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.code_look_checker import _llm_code_look_decision
from ai_tech_lead.completion_verifier import verify_completion
from ai_tech_lead.orchestrator_llm import OrchestratorLlmResult
from ai_tech_lead.project_guidance_governance import review_project_guidance
from ai_tech_lead.request_relevance import _llm_relevance_decision
from ai_tech_lead.risk_reviewer import _llm_risk_decision
from ai_tech_lead.tech_lead_analyst import analyse_task


def _settings():
    return replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)


def test_structured_consumers_share_strict_schema_boundary(monkeypatch) -> None:
    payloads = {
        "atl_relevance": {"atl_relevant": True, "reason": "Technical task."},
        "code_look_need": {"needs_code_look": True, "reason": "Existing code matters."},
        "risk_review": {
            "needs_approval": True,
            "risk_level": "MEDIUM",
            "reason": "Bounded code change.",
            "confidence": 0.95,
        },
        "tech_lead_analysis": {
            "task_statement": "Fix the bounded defect.",
            "tech_direction": "Change only the affected boundary and test it.",
        },
        "project_guidance_governance": {
            "status": "sufficient",
            "requires_review": False,
            "summary": "",
            "related_locations": [],
            "proposed_change": "",
            "reason": "Existing guidance is enough.",
        },
        "completion_verification": {
            "status": "complete",
            "reason": "Acceptance criteria and tests pass.",
            "correction": "",
        },
    }
    seen: dict[str, dict] = {}

    def fake_provider(**kwargs):
        name = kwargs["json_schema_name"]
        seen[name] = kwargs["json_schema"]
        return OrchestratorLlmResult(text=json.dumps(payloads[name]), tokens_in=10, tokens_out=10)

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_provider)
    settings = _settings()

    assert _llm_relevance_decision("Fix the API bug", settings).is_atl_relevant is True
    assert _llm_code_look_decision("Fix the API bug", settings).needs_code_look is True
    assert _llm_risk_decision("Fix the API bug", settings).risk_level == "MEDIUM"
    assert analyse_task("Fix the API bug", [], "", [], settings).task_statement
    assert review_project_guidance("Fix the API bug", [], settings).status == "sufficient"
    assert (
        verify_completion(
            bounded_request="Fix the API bug",
            formulated_task="Fix the API bug",
            brief="Bounded change",
            plan_text="1. Fix boundary\nDone when tests pass",
            acceptance_criteria=["Tests pass"],
            changed_files=("src/ai_tech_lead/example.py",),
            coding_agent_result="Tests pass.",
            settings=settings,
        ).status
        == "complete"
    )

    assert set(seen) == set(payloads)
    for schema in seen.values():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"]
