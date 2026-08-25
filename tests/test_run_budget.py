from __future__ import annotations

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.run_budget import (
    RunBudgetExceededError,
    RunBudgetTracker,
    begin_active_run_budget_call,
    record_active_run_budget_usage,
    use_run_budget,
)


def test_from_settings_reads_the_configured_ceilings_and_persisted_usage() -> None:
    settings = parse_settings(
        {
            **valid_settings_dict(),
            "orchestrator_run_max_calls": 3,
            "orchestrator_run_max_tokens": 1000,
            "orchestrator_run_max_cost_usd": 0.5,
        }
    )
    tracker = RunBudgetTracker.from_settings(
        settings,
        calls_used=1,
        tokens_in_used=100,
        tokens_out_used=50,
        cost_usd_used=0.1,
    )
    assert tracker.max_calls == 3
    assert tracker.max_tokens == 1000
    assert tracker.max_cost_usd == 0.5
    assert tracker.calls_used == 1
    assert tracker.tokens_used == 150
    assert tracker.cost_usd_used == pytest.approx(0.1)


def test_begin_call_is_exact_for_call_ceiling() -> None:
    tracker = RunBudgetTracker(max_calls=2, max_tokens=100_000, max_cost_usd=100.0)
    tracker.begin_call()
    tracker.begin_call()
    assert tracker.calls_used == 2
    with pytest.raises(RunBudgetExceededError, match="call ceiling"):
        tracker.begin_call()
    assert tracker.calls_used == 2


def test_record_usage_accumulates_input_output_and_cost() -> None:
    tracker = RunBudgetTracker(max_calls=5, max_tokens=1000, max_cost_usd=1.0)
    tracker.begin_call()
    tracker.record_usage(tokens_in=100, tokens_out=50, cost_usd=0.1)
    tracker.begin_call()
    tracker.record_usage(tokens_in=80, tokens_out=20, cost_usd=0.05)
    assert tracker.calls_used == 2
    assert tracker.tokens_in_used == 180
    assert tracker.tokens_out_used == 70
    assert tracker.tokens_used == 250
    assert tracker.cost_usd_used == pytest.approx(0.15)


def test_token_ceiling_is_metered_then_blocks_next_provider_attempt() -> None:
    tracker = RunBudgetTracker(max_calls=100, max_tokens=100, max_cost_usd=100.0)
    tracker.begin_call()
    tracker.record_usage(tokens_in=60, tokens_out=50, cost_usd=0.0)
    assert tracker.tokens_used == 110
    with pytest.raises(RunBudgetExceededError, match="token ceiling"):
        tracker.begin_call()
    assert tracker.calls_used == 1


def test_cost_ceiling_is_metered_then_blocks_next_provider_attempt() -> None:
    tracker = RunBudgetTracker(max_calls=100, max_tokens=100_000, max_cost_usd=1.0)
    tracker.begin_call()
    tracker.record_usage(tokens_in=1, tokens_out=1, cost_usd=1.5)
    with pytest.raises(RunBudgetExceededError, match="cost ceiling"):
        tracker.begin_call()
    assert tracker.calls_used == 1


def test_active_budget_context_counts_provider_boundary_and_does_not_leak() -> None:
    tracker = RunBudgetTracker(max_calls=5, max_tokens=1000, max_cost_usd=1.0)
    with use_run_budget(tracker):
        begin_active_run_budget_call()
        record_active_run_budget_usage(tokens_in=12, tokens_out=8, cost_usd=0.02)
    assert tracker.calls_used == 1
    assert tracker.tokens_used == 20
    assert tracker.cost_usd_used == pytest.approx(0.02)

    # No active coding-run budget: unrelated orchestrator callers are untouched.
    begin_active_run_budget_call()
    record_active_run_budget_usage(tokens_in=99, tokens_out=99, cost_usd=0.9)
    assert tracker.calls_used == 1
    assert tracker.tokens_used == 20
