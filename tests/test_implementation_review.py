from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import (
    DEFAULT_IMPLEMENTATION_REVIEW_RISK_KEYWORDS,
    parse_settings,
)
from ai_tech_lead import implementation_review as ir


def _settings(**overrides):
    base = replace(
        parse_settings(valid_settings_dict()),
        coding_agent_command="codex",
        review_agent_command="claude",
        review_agent_args=[],
        implementation_review_enabled=True,
        implementation_review_risk_keywords=list(DEFAULT_IMPLEMENTATION_REVIEW_RISK_KEYWORDS),
        execute_coding_agent=True,
    )
    return replace(base, **overrides) if overrides else base


def _fake_result(**kw):
    return SimpleNamespace(
        stdout=kw.get("stdout", ""),
        stderr=kw.get("stderr", ""),
        returncode=kw.get("returncode", 0),
        changed_files_delta=kw.get("changed_files_delta", ()),
        timed_out=kw.get("timed_out", False),
        cancelled=kw.get("cancelled", False),
        message=kw.get("message", ""),
    )


def _run(monkeypatch, result):
    monkeypatch.setattr(
        ir, "run_coding_agent", lambda **_kw: result if not isinstance(result, Exception) else _raise(result)
    )


def _raise(exc):
    raise exc


# --- policy ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,risk,text,expected",
    [
        ("light", "LOW", "tidy the readme", False),
        ("standard", "LOW", "tidy the readme", True),
        ("deep", "LOW", "tidy the readme", True),
        ("light", "MEDIUM", "tidy the readme", True),
        ("light", "UNKNOWN", "tidy the readme", True),
        ("light", "LOW", "rotate the auth token", True),  # keyword
        ("light", "LOW", "run a database migration", True),  # keyword
    ],
)
def test_review_required_policy(tier, risk, text, expected) -> None:
    assert (
        ir.review_required(
            coding_agent_tier=tier,
            risk_level=risk,
            risk_signal_text=text,
            settings=_settings(),
        )
        is expected
    )


def test_review_disabled_by_setting() -> None:
    assert (
        ir.review_required(
            coding_agent_tier="deep",
            risk_level="HIGH",
            risk_signal_text="security migration",
            settings=_settings(implementation_review_enabled=False),
        )
        is False
    )


# --- reviewer identity / availability -----------------------------------


def test_reviewer_unavailable_when_command_matches_implementer(monkeypatch) -> None:
    monkeypatch.setattr(ir, "run_coding_agent", lambda **_kw: _fake_result())
    outcome = _call(monkeypatch, settings=_settings(review_agent_command="codex"))
    assert outcome.status == ir.STATUS_UNAVAILABLE
    assert outcome.performed_by == ""


def test_reviewer_unavailable_when_not_configured() -> None:
    assert ir.reviewer_available(_settings(review_agent_command="")) is False


def test_reviewer_unavailable_on_preflight_failure(monkeypatch) -> None:
    def _boom(**_kw):
        raise RuntimeError("coding agent backend preflight failed: command not found")

    monkeypatch.setattr(ir, "run_coding_agent", _boom)
    outcome = _call(monkeypatch)
    assert outcome.status == ir.STATUS_UNAVAILABLE


# --- output parsing ----------------------------------------------------


def _call(monkeypatch, *, settings=None):
    return ir.run_implementation_review(
        formulated_task="Add a logout button",
        plan_text="1. add button",
        acceptance_criteria=["Logout works"],
        diff_summary="M\tsrc/app.py",
        changed_files=("src/app.py",),
        validation_command="uv run pytest -q",
        validation_passed=True,
        validation_output_tail="1 passed",
        checkout_root=Path("/tmp"),
        settings=settings or _settings(),
    )


def test_clean_review_when_pass_and_no_required(monkeypatch) -> None:
    monkeypatch.setattr(
        ir,
        "run_coding_agent",
        lambda **_kw: _fake_result(
            stdout="FINDING [advisory] src/app.py: consider a docstring.\nREVIEW: pass\n"
        ),
    )
    outcome = _call(monkeypatch)
    assert outcome.status == ir.STATUS_CLEAN
    assert outcome.correction == ""
    assert outcome.findings == ("[advisory] src/app.py: consider a docstring.",)
    assert outcome.performed_by == "claude"


def test_findings_when_changes_required_with_required_finding(monkeypatch) -> None:
    monkeypatch.setattr(
        ir,
        "run_coding_agent",
        lambda **_kw: _fake_result(
            stdout=(
                "FINDING [required] Logout works: the handler is never wired.\n"
                "FINDING [advisory] src/app.py: rename the helper.\n"
                "REVIEW: changes-required\n"
            )
        ),
    )
    outcome = _call(monkeypatch)
    assert outcome.status == ir.STATUS_FINDINGS
    assert "handler is never wired" in outcome.correction
    assert len(outcome.findings) == 2


def test_failed_when_no_verdict_line(monkeypatch) -> None:
    monkeypatch.setattr(
        ir,
        "run_coding_agent",
        lambda **_kw: _fake_result(stdout="Looks fine to me, no problems."),
    )
    assert _call(monkeypatch).status == ir.STATUS_FAILED


def test_failed_when_changes_required_without_required_finding(monkeypatch) -> None:
    monkeypatch.setattr(
        ir,
        "run_coding_agent",
        lambda **_kw: _fake_result(
            stdout="FINDING [advisory] x: nit.\nREVIEW: changes-required\n"
        ),
    )
    assert _call(monkeypatch).status == ir.STATUS_FAILED


def test_mutated_when_reviewer_changes_files(monkeypatch) -> None:
    monkeypatch.setattr(
        ir,
        "run_coding_agent",
        lambda **_kw: _fake_result(
            stdout="REVIEW: pass\n", changed_files_delta=("src/app.py",)
        ),
    )
    outcome = _call(monkeypatch)
    assert outcome.status == ir.STATUS_MUTATED
    assert outcome.performed_by == "claude"


def test_failed_when_reviewer_run_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        ir,
        "run_coding_agent",
        lambda **_kw: _fake_result(stdout="REVIEW: pass\n", returncode=1),
    )
    assert _call(monkeypatch).status == ir.STATUS_FAILED
