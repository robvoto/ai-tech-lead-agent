"""Per-run orchestrator cost/token/call ceiling tracker (ATL-036 acceptance
criterion 11).

Deliberately NOT wired into coding_workflow_graph.py yet: that graph has no
existing per-run cost accumulator to hook into today (checked while building
this — no node currently sums tokens/cost across the run), so wiring a
ceiling into every orchestrator-calling node is a separate, reviewed change,
not something to bolt on inside this ticket's pass. This module is built and
tested so that follow-up is plumbing, not design — the two open questions
below are answered here, not left for whoever wires it in.

Entry-point behavior, decided up front:
  - Telegram (interactive): the caller catches RunBudgetExceededError and
    tells the operator the run stopped, with instructions to raise the
    ceiling in settings and resume — no automatic mid-run expansion of the
    budget, ever.
  - Agent Hub / subprocess (run-agent-task): there is no one to ask
    synchronously, so the same exception must propagate and fail the
    subprocess closed (a clear error status through the JSON contract),
    never hang waiting for input that will never arrive.
Both paths raise the same exception; only how each caller responds differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .app_settings import AppSettings


class RunBudgetExceededError(RuntimeError):
    """Raised when the next orchestrator call would exceed this run's ceiling.

    Never raised retroactively for a call already made — checked before each
    call so the budget is never silently expanded past its ceiling.
    """


@dataclass
class RunBudgetTracker:
    """Accumulates one run's orchestrator call/token/cost usage and enforces
    its ceilings. One instance per run (e.g. per request_id); not shared
    across runs or threads."""

    max_calls: int
    max_tokens: int
    max_cost_usd: float
    calls_used: int = field(default=0)
    tokens_used: int = field(default=0)
    cost_usd_used: float = field(default=0.0)

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "RunBudgetTracker":
        return cls(
            max_calls=settings.orchestrator_run_max_calls,
            max_tokens=settings.orchestrator_run_max_tokens,
            max_cost_usd=settings.orchestrator_run_max_cost_usd,
        )

    def check_before_call(self) -> None:
        """Raise RunBudgetExceededError if the ceiling is already reached —
        called before making the next call, so a run stops at its ceiling
        rather than one call past it."""

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

    def record_call(self, *, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        """Record a completed call's usage. Does not itself raise — the next
        check_before_call() catches a ceiling this call just reached/passed,
        so a run stops before its *next* call rather than mid-call."""

        self.calls_used += 1
        self.tokens_used += tokens_in + tokens_out
        self.cost_usd_used += cost_usd
