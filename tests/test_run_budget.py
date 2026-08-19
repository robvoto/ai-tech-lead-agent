from __future__ import annotations

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.run_budget import RunBudgetExceededError, RunBudgetTracker


def test_from_settings_reads_the_configured_ceilings() -> None:
    settings = parse_settings(
        {
            **valid_settings_dict(),
            "orchestrator_run_max_calls": 3,
            "orchestrator_run_max_tokens": 1000,
            "orchestrator_run_max_cost_usd": 0.5,
        }
    )
    tracker = RunBudgetTracker.from_settings(settings)
    assert tracker.max_calls == 3
    assert tracker.max_tokens == 1000
    assert tracker.max_cost_usd == 0.5


def test_check_before_call_passes_when_under_every_ceiling() -> None:
    tracker = RunBudgetTracker(max_calls=5, max_tokens=1000, max_cost_usd=1.0)
    tracker.check_before_call()  # must not raise


def test_record_call_accumulates_usage() -> None:
    tracker = RunBudgetTracker(max_calls=5, max_tokens=1000, max_cost_usd=1.0)
    tracker.record_call(tokens_in=100, tokens_out=50, cost_usd=0.1)
    tracker.record_call(tokens_in=100, tokens_out=50, cost_usd=0.1)
    assert tracker.calls_used == 2
    assert tracker.tokens_used == 300
    assert tracker.cost_usd_used == pytest.approx(0.2)


def test_call_ceiling_stops_the_next_call_not_the_one_that_reached_it() -> None:
    tracker = RunBudgetTracker(max_calls=2, max_tokens=100_000, max_cost_usd=100.0)
    tracker.check_before_call()
    tracker.record_call(tokens_in=1, tokens_out=1, cost_usd=0.0)
    tracker.check_before_call()
    tracker.record_call(tokens_in=1, tokens_out=1, cost_usd=0.0)
    with pytest.raises(RunBudgetExceededError, match="call ceiling"):
        tracker.check_before_call()


def test_token_ceiling_raises() -> None:
    tracker = RunBudgetTracker(max_calls=100, max_tokens=100, max_cost_usd=100.0)
    tracker.record_call(tokens_in=60, tokens_out=50, cost_usd=0.0)
    with pytest.raises(RunBudgetExceededError, match="token ceiling"):
        tracker.check_before_call()


def test_cost_ceiling_raises() -> None:
    tracker = RunBudgetTracker(max_calls=100, max_tokens=100_000, max_cost_usd=1.0)
    tracker.record_call(tokens_in=1, tokens_out=1, cost_usd=1.5)
    with pytest.raises(RunBudgetExceededError, match="cost ceiling"):
        tracker.check_before_call()


def test_record_call_never_raises_even_past_ceiling() -> None:
    # Recording is retrospective bookkeeping; the ceiling is enforced on the
    # *next* check_before_call, never by refusing to record what happened.
    tracker = RunBudgetTracker(max_calls=1, max_tokens=100_000, max_cost_usd=100.0)
    tracker.record_call(tokens_in=1, tokens_out=1, cost_usd=0.0)
    tracker.record_call(tokens_in=1, tokens_out=1, cost_usd=0.0)
    assert tracker.calls_used == 2
