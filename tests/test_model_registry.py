from __future__ import annotations

import pytest

from ai_tech_lead.model_registry import ModelRegistryError, estimate_cost, get_model_spec


def test_get_model_spec_exact_match_returns_registered_model() -> None:
    spec = get_model_spec("gpt-4.1-mini")
    assert spec.model_id == "gpt-4.1-mini"
    assert spec.reasoning is None
    assert spec.pricing.input_per_million == 0.40


def test_get_model_spec_prefix_match_handles_dated_model_names() -> None:
    spec = get_model_spec("gpt-4.1-mini-2025-04-14")
    assert spec.model_id == "gpt-4.1-mini"


def test_get_model_spec_unknown_model_raises() -> None:
    with pytest.raises(ModelRegistryError):
        get_model_spec("not-a-real-model")


def test_reasoning_model_records_supported_efforts_and_default() -> None:
    spec = get_model_spec("gpt-5.6-luna")
    assert spec.reasoning is not None
    assert spec.reasoning.default_effort == "medium"
    assert spec.reasoning.requires_explicit_effort is True
    assert "xhigh" in spec.reasoning.supported_efforts
    assert "max" in spec.reasoning.supported_efforts


def test_estimate_cost_known_model_is_confirmed() -> None:
    estimate = estimate_cost("gpt-4.1-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate.is_estimated is True
    assert estimate.usd == pytest.approx(0.40 + 1.60)


def test_estimate_cost_unknown_model_is_unresolved_not_silently_zero() -> None:
    estimate = estimate_cost("not-a-real-model", input_tokens=1000, output_tokens=1000)
    assert estimate.is_estimated is False
    assert estimate.usd == 0.0


def test_luna_is_cheaper_than_gpt_4_1_mini_on_both_input_and_output() -> None:
    mini = get_model_spec("gpt-4.1-mini").pricing
    luna = get_model_spec("gpt-5.6-luna").pricing
    assert luna.input_per_million < mini.input_per_million
    assert luna.output_per_million < mini.output_per_million
