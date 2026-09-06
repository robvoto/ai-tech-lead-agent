"""Tests for the adapter-neutral coding-agent result contract (ATL-040)."""

from __future__ import annotations

from dataclasses import dataclass

from ai_tech_lead.coding_agent_contract import (
    CodingAgentReport,
    normalize_coding_agent_result,
)


@dataclass
class _FullResult:
    """Stand-in for coding_agent_runner.CodingAgentResult."""

    success: bool
    returncode: int | None
    stdout: str
    message: str
    changed_files_delta: tuple[str, ...] = ()
    timed_out: bool = False
    cancelled: bool = False


class _PartialAdapterResult:
    """A minimal, non-JSON adapter result exposing only a subset of fields.

    No `success`, `changed_files_delta`, or `message` attribute at all —
    the normalizer must not raise and must not invent values for them.
    """

    def __init__(self, *, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_full_result_normalizes_all_contract_fields() -> None:
    result = _FullResult(
        success=True,
        returncode=0,
        stdout=(
            "Inspected foo.py and bar.py.\n"
            "Implemented the fix.\n"
            "Validation: uv run pytest tests/test_foo.py — passed"
        ),
        message="The coding agent finished successfully. System exit code: 0.",
        changed_files_delta=("src/foo.py",),
    )

    report = normalize_coding_agent_result(result)

    assert report == CodingAgentReport(
        success=True,
        returncode=0,
        changed_files=("src/foo.py",),
        validation="uv run pytest tests/test_foo.py — passed",
        summary="The coding agent finished successfully. System exit code: 0.",
    )


def test_partial_non_json_adapter_result_normalizes_without_guessing() -> None:
    result = _PartialAdapterResult(
        returncode=0,
        stdout="Plain prose output with no structured fields at all.",
    )

    report = normalize_coding_agent_result(result)

    assert report.success is True
    assert report.returncode == 0
    assert report.changed_files == ()
    assert report.validation == ""
    assert report.summary == ""


def test_validation_is_absent_when_agent_did_not_state_one() -> None:
    result = _FullResult(
        success=True,
        returncode=0,
        stdout="Implemented the fix. No mention of validation at all.",
        message="Done.",
    )

    report = normalize_coding_agent_result(result)

    assert report.validation == ""


def test_validation_line_is_bounded() -> None:
    long_value = "x" * 300
    result = _FullResult(
        success=True,
        returncode=0,
        stdout=f"Validation: {long_value}",
        message="Done.",
    )

    report = normalize_coding_agent_result(result)

    assert len(report.validation) <= 200
    assert report.validation.endswith("…")


def test_explicit_success_field_overrides_returncode() -> None:
    result = _FullResult(
        success=False,
        returncode=0,
        stdout="",
        message="ALREADY_DONE: task was already implemented.",
    )

    report = normalize_coding_agent_result(result)

    assert report.success is False


def test_timeout_and_cancellation_are_never_treated_as_success_without_explicit_flag() -> None:
    class _NoSuccessField:
        def __init__(self, *, returncode: int | None, timed_out: bool, cancelled: bool) -> None:
            self.returncode = returncode
            self.timed_out = timed_out
            self.cancelled = cancelled
            self.stdout = ""

    timed_out_result = _NoSuccessField(returncode=None, timed_out=True, cancelled=False)
    cancelled_result = _NoSuccessField(returncode=None, timed_out=False, cancelled=True)

    assert normalize_coding_agent_result(timed_out_result).success is False
    assert normalize_coding_agent_result(cancelled_result).success is False
