"""Per-run orchestrator call/token/cost budget enforcement.

The coding workflow persists usage counters in LangGraph state.  During one
orchestrator-calling node, that persisted usage is loaded into a
:class:`RunBudgetTracker` and activated only for the provider call(s) made by
that node.  The low-level Responses API boundary records every actual provider
attempt, including internal JSON retries and web-search calls.

Budget semantics are deliberately explicit:
- The call-attempt ceiling is a true pre-call hard limit.  Once ``max_calls``
  attempts have started, another provider request is not sent.
- Token and estimated-cost ceilings are metered from provider-reported usage.
  The response size/cost of a call cannot be known exactly before it runs, so a
  completed call may be the call that reaches or crosses either ceiling.  Once
  that happens, no subsequent provider request is sent.
- No ceiling is ever raised automatically.

Entry-point behaviour is owned by the graph: interactive Telegram runs pause on
an explicit budget interrupt so the operator can change settings and retry the
blocked step; non-interactive Agent Hub/subprocess runs fail closed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from .app_settings import AppSettings


class RunBudgetExceededError(RuntimeError):
    """Raised before a provider request when the persisted run budget is exhausted."""


@dataclass
class RunBudgetTracker:
    """Track one coding workflow run's orchestrator usage against configured ceilings."""

    max_calls: int
    max_tokens: int
    max_cost_usd: float
    calls_used: int = field(default=0)
    tokens_in_used: int = field(default=0)
    tokens_out_used: int = field(default=0)
    cost_usd_used: float = field(default=0.0)

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        *,
        calls_used: int = 0,
        tokens_in_used: int = 0,
        tokens_out_used: int = 0,
        cost_usd_used: float = 0.0,
    ) -> "RunBudgetTracker":
        return cls(
            max_calls=settings.orchestrator_run_max_calls,
            max_tokens=settings.orchestrator_run_max_tokens,
            max_cost_usd=settings.orchestrator_run_max_cost_usd,
            calls_used=calls_used,
            tokens_in_used=tokens_in_used,
            tokens_out_used=tokens_out_used,
            cost_usd_used=cost_usd_used,
        )

    @property
    def tokens_used(self) -> int:
        return self.tokens_in_used + self.tokens_out_used

    def check_before_call(self) -> None:
        """Reject a new provider request when any already-metered ceiling is exhausted."""

        if self.calls_used >= self.max_calls:
            raise RunBudgetExceededError(
                f"Run call ceiling reached: {self.calls_used}/{self.max_calls} calls used."
            )
        if self.tokens_used >= self.max_tokens:
            raise RunBudgetExceededError(
                f"Run token ceiling reached: {self.tokens_used}/{self.max_tokens} tokens used."
            )
        if self.cost_usd_used >= self.max_cost_usd:
            raise RunBudgetExceededError(
                f"Run cost ceiling reached: ${self.cost_usd_used:.4f}/"
                f"${self.max_cost_usd:.4f} used."
            )

    def begin_call(self) -> None:
        """Reserve one provider-call attempt after checking all current ceilings."""

        self.check_before_call()
        self.calls_used += 1

    def record_usage(self, *, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        """Record provider-reported usage for an attempt that already began.

        Recording is retrospective and therefore never rejects the response
        that produced the usage.  A subsequent :meth:`begin_call` is what
        prevents any further provider request after a token/cost ceiling has
        been reached or crossed.
        """

        self.tokens_in_used += max(0, tokens_in)
        self.tokens_out_used += max(0, tokens_out)
        self.cost_usd_used += max(0.0, cost_usd)


_ACTIVE_RUN_BUDGET: ContextVar[RunBudgetTracker | None] = ContextVar(
    "ai_tech_lead_active_run_budget", default=None
)


@contextmanager
def use_run_budget(tracker: RunBudgetTracker) -> Iterator[RunBudgetTracker]:
    """Activate ``tracker`` only for the current execution context."""

    token = _ACTIVE_RUN_BUDGET.set(tracker)
    try:
        yield tracker
    finally:
        _ACTIVE_RUN_BUDGET.reset(token)


def begin_active_run_budget_call() -> None:
    """Count/check one actual provider attempt when a coding-run budget is active."""

    tracker = _ACTIVE_RUN_BUDGET.get()
    if tracker is not None:
        tracker.begin_call()


def record_active_run_budget_usage(
    *, tokens_in: int, tokens_out: int, cost_usd: float
) -> None:
    """Meter provider-reported usage when a coding-run budget is active."""

    tracker = _ACTIVE_RUN_BUDGET.get()
    if tracker is not None:
        tracker.record_usage(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)
