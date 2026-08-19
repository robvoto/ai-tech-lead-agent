from __future__ import annotations

import pytest
from helpers import valid_settings_dict

import ai_tech_lead.execution_profiles as execution_profiles
from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.execution_profiles import (
    NORMAL_PROFILE,
    NORMAL_TIER,
    SIMPLE_PROFILE,
    SIMPLE_TIER,
    STRONG_PROFILE,
    STRONG_TIER,
    ExecutionProfile,
    ExecutionProfileError,
    _validate_tier_ceiling,
    next_escalation_effort,
    resolve_execution_profile,
    resolve_profile_for_purpose,
    validate_registry_ceilings,
)


def _settings(**overrides):
    raw = valid_settings_dict()
    raw.update(overrides)
    return parse_settings(raw)


def test_normal_profile_mirrors_configured_model_with_no_reasoning_effort() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-4.1-mini", orchestrator_ai_max_output_tokens=300
    )
    profile = resolve_execution_profile(NORMAL_PROFILE, settings=settings)
    assert profile.model == "gpt-4.1-mini"
    assert profile.tier == NORMAL_TIER
    assert profile.reasoning_effort is None
    assert profile.max_output_tokens == 300


def test_normal_profile_requires_settings() -> None:
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile(NORMAL_PROFILE)


def test_normal_profile_min_output_tokens_raises_the_floor() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-4.1-mini", orchestrator_ai_max_output_tokens=300
    )
    profile = resolve_execution_profile(NORMAL_PROFILE, settings=settings, min_output_tokens=800)
    assert profile.max_output_tokens == 800


def test_normal_profile_unknown_configured_model_raises() -> None:
    settings = _settings(orchestrator_ai_model="not-a-real-model")
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile(NORMAL_PROFILE, settings=settings)


def test_simple_profile_uses_luna_at_none_effort_per_live_benchmark() -> None:
    # SIMPLE_PROFILE migrated from gpt-4.1-mini to gpt-5.6-luna @ "none" on
    # 2026-08-19 based on a live benchmark (see execution_profiles.py's
    # _STATIC_PROFILES comment for the evidence and its caveats).
    profile = resolve_execution_profile(SIMPLE_PROFILE)
    assert profile.model == "gpt-5.6-luna"
    assert profile.tier == SIMPLE_TIER
    assert profile.reasoning_effort == "none"


def test_strong_profile_uses_luna_with_explicit_high_reasoning_effort() -> None:
    profile = resolve_execution_profile(STRONG_PROFILE)
    assert profile.model == "gpt-5.6-luna"
    assert profile.tier == STRONG_TIER
    assert profile.reasoning_effort == "high"
    assert profile.max_output_tokens >= 1024


def test_unknown_profile_name_raises() -> None:
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile("nonexistent")


def test_normal_profile_auto_sets_default_reasoning_effort_for_a_reasoning_model() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-5.6-luna", orchestrator_ai_max_output_tokens=2000
    )
    profile = resolve_execution_profile(NORMAL_PROFILE, settings=settings)
    assert profile.reasoning_effort == "medium"


def test_normal_profile_reasoning_model_below_output_floor_raises() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-5.6-luna", orchestrator_ai_max_output_tokens=100
    )
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile(NORMAL_PROFILE, settings=settings)


def test_normal_tier_ceiling_allows_lunas_own_default_of_medium() -> None:
    # Luna's own registry default is "medium", which is exactly the "normal"
    # ceiling — this proves the ceiling check runs, not just that it never fires.
    settings = _settings(
        orchestrator_ai_model="gpt-5.6-luna", orchestrator_ai_max_output_tokens=2000
    )
    profile = resolve_profile_for_purpose("risk_review", settings=settings)
    assert profile.reasoning_effort == "medium"


def test_resolve_profile_for_purpose_unknown_purpose_raises() -> None:
    with pytest.raises(ExecutionProfileError):
        resolve_profile_for_purpose("not-a-real-purpose")


def test_resolve_profile_for_purpose_simple_call_site_stays_simple() -> None:
    profile = resolve_profile_for_purpose("request_relevance")
    assert profile.tier == SIMPLE_TIER


def test_resolve_profile_for_purpose_strong_call_site_stays_strong() -> None:
    profile = resolve_profile_for_purpose("tech_lead_analysis")
    assert profile.tier == STRONG_TIER


def test_validate_tier_ceiling_rejects_an_effort_above_the_tier_ceiling() -> None:
    # A directly-constructed profile — bypassing resolve_execution_profile's
    # own guardrails — still gets caught by the ceiling check itself, proving
    # it is a real independent safety net and not just dead code.
    profile = ExecutionProfile(
        name="hypothetical",
        tier=SIMPLE_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        max_output_tokens=2000,
    )
    with pytest.raises(ExecutionProfileError):
        _validate_tier_ceiling(SIMPLE_TIER, profile)


def test_validate_tier_ceiling_rejects_never_automatic_efforts() -> None:
    profile = ExecutionProfile(
        name="hypothetical",
        tier=STRONG_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="xhigh",
        max_output_tokens=2000,
    )
    with pytest.raises(ExecutionProfileError):
        _validate_tier_ceiling(STRONG_TIER, profile)


def test_resolve_profile_for_purpose_with_override_tier_uses_the_override() -> None:
    profile = resolve_profile_for_purpose("request_relevance", override_tier=STRONG_TIER)
    assert profile.tier == STRONG_TIER
    assert profile.model == "gpt-5.6-luna"


def test_next_escalation_effort_steps_up_within_tier_ceiling() -> None:
    assert next_escalation_effort(SIMPLE_TIER, "none") == "low"


def test_next_escalation_effort_returns_none_at_tier_ceiling() -> None:
    assert next_escalation_effort(SIMPLE_TIER, "low") is None


def test_next_escalation_effort_never_crosses_into_a_higher_tier() -> None:
    # "normal"'s ceiling is medium — stepping from medium must not reach "high",
    # which belongs to "strong".
    assert next_escalation_effort(NORMAL_TIER, "medium") is None


def test_next_escalation_effort_none_for_a_non_reasoning_model() -> None:
    assert next_escalation_effort(SIMPLE_TIER, None) is None


def test_validate_registry_ceilings_passes_for_the_shipped_profiles() -> None:
    validate_registry_ceilings()


def test_purpose_profile_override_is_used_when_present(monkeypatch) -> None:
    override_profile = ExecutionProfile(
        name="operator_question_luna_none",
        tier=NORMAL_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="none",
        max_output_tokens=300,
    )
    monkeypatch.setitem(
        execution_profiles.PURPOSE_PROFILE_OVERRIDE, "operator_question", override_profile
    )

    profile = resolve_profile_for_purpose("operator_question")

    assert profile.model == "gpt-5.6-luna"
    assert profile.reasoning_effort == "none"


def test_purpose_profile_override_min_output_tokens_raises_the_floor(monkeypatch) -> None:
    override_profile = ExecutionProfile(
        name="operator_question_luna_none",
        tier=NORMAL_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="none",
        max_output_tokens=300,
    )
    monkeypatch.setitem(
        execution_profiles.PURPOSE_PROFILE_OVERRIDE, "operator_question", override_profile
    )

    profile = resolve_profile_for_purpose("operator_question", min_output_tokens=900)

    assert profile.max_output_tokens == 900


def test_purpose_profile_override_still_enforces_its_tier_ceiling(monkeypatch) -> None:
    breaching_profile = ExecutionProfile(
        name="operator_question_bad",
        tier=NORMAL_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        max_output_tokens=2000,
    )
    monkeypatch.setitem(
        execution_profiles.PURPOSE_PROFILE_OVERRIDE, "operator_question", breaching_profile
    )

    with pytest.raises(ExecutionProfileError):
        resolve_profile_for_purpose("operator_question")


def test_explicit_operator_override_tier_wins_over_purpose_profile_override(monkeypatch) -> None:
    override_profile = ExecutionProfile(
        name="operator_question_luna_none",
        tier=NORMAL_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="none",
        max_output_tokens=300,
    )
    monkeypatch.setitem(
        execution_profiles.PURPOSE_PROFILE_OVERRIDE, "operator_question", override_profile
    )

    profile = resolve_profile_for_purpose("operator_question", override_tier=STRONG_TIER)

    assert profile.tier == STRONG_TIER
    assert profile.reasoning_effort == "high"


def test_validate_registry_ceilings_rejects_a_breaching_purpose_override(monkeypatch) -> None:
    breaching_profile = ExecutionProfile(
        name="operator_question_bad",
        tier=NORMAL_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        max_output_tokens=2000,
    )
    monkeypatch.setitem(
        execution_profiles.PURPOSE_PROFILE_OVERRIDE, "operator_question", breaching_profile
    )

    with pytest.raises(ExecutionProfileError):
        validate_registry_ceilings()


def test_validate_registry_ceilings_rejects_an_override_for_an_unknown_purpose(monkeypatch) -> None:
    stray_profile = ExecutionProfile(
        name="stray", tier=NORMAL_TIER, model="gpt-4.1-mini", reasoning_effort=None,
        max_output_tokens=300,
    )
    monkeypatch.setitem(
        execution_profiles.PURPOSE_PROFILE_OVERRIDE, "not-a-real-purpose", stray_profile
    )

    with pytest.raises(ExecutionProfileError):
        validate_registry_ceilings()
