from __future__ import annotations

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.execution_profiles import (
    DEEP_PROFILE,
    LOW_PROFILE,
    STANDARD_PROFILE,
    ExecutionProfileError,
    resolve_execution_profile,
)


def _settings(**overrides):
    raw = valid_settings_dict()
    raw.update(overrides)
    return parse_settings(raw)


def test_standard_profile_mirrors_configured_model_with_no_reasoning_effort() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-4.1-mini", orchestrator_ai_max_output_tokens=300
    )
    profile = resolve_execution_profile(STANDARD_PROFILE, settings=settings)
    assert profile.model == "gpt-4.1-mini"
    assert profile.reasoning_effort is None
    assert profile.max_output_tokens == 300


def test_standard_profile_requires_settings() -> None:
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile(STANDARD_PROFILE)


def test_standard_profile_min_output_tokens_raises_the_floor() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-4.1-mini", orchestrator_ai_max_output_tokens=300
    )
    profile = resolve_execution_profile(STANDARD_PROFILE, settings=settings, min_output_tokens=800)
    assert profile.max_output_tokens == 800


def test_standard_profile_unknown_configured_model_raises() -> None:
    settings = _settings(orchestrator_ai_model="not-a-real-model")
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile(STANDARD_PROFILE, settings=settings)


def test_low_profile_is_a_valid_non_reasoning_profile() -> None:
    profile = resolve_execution_profile(LOW_PROFILE)
    assert profile.model == "gpt-4.1-mini"
    assert profile.reasoning_effort is None


def test_deep_profile_uses_luna_with_explicit_high_reasoning_effort() -> None:
    profile = resolve_execution_profile(DEEP_PROFILE)
    assert profile.model == "gpt-5.6-luna"
    assert profile.reasoning_effort == "high"
    assert profile.max_output_tokens >= 1024


def test_unknown_profile_name_raises() -> None:
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile("nonexistent")


def test_standard_profile_auto_sets_default_reasoning_effort_for_a_reasoning_model() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-5.6-luna", orchestrator_ai_max_output_tokens=2000
    )
    profile = resolve_execution_profile(STANDARD_PROFILE, settings=settings)
    assert profile.reasoning_effort == "medium"


def test_standard_profile_reasoning_model_below_output_floor_raises() -> None:
    settings = _settings(
        orchestrator_ai_model="gpt-5.6-luna", orchestrator_ai_max_output_tokens=100
    )
    with pytest.raises(ExecutionProfileError):
        resolve_execution_profile(STANDARD_PROFILE, settings=settings)
