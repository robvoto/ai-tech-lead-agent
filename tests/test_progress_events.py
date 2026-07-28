from __future__ import annotations

import io
import json
import time
from typing import Any

from ai_tech_lead.progress_events import (
    MAX_PROGRESS_SUMMARY_CHARS,
    ProgressReporter,
    StdoutJsonlProgressSink,
    emit_terminal_progress,
    progress_reporter_from_input,
)


def _events(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_stdout_sink_emits_correlated_monotonic_jsonl() -> None:
    stream = io.StringIO()
    sink = StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)

    assert sink.emit(
        event_type="phase",
        phase="planning",
        human_summary="Preparing the plan.",
        metadata={"attempt": 1, "secret": "must not pass"},
    )
    assert sink.emit(
        event_type="heartbeat",
        phase="planning",
        human_summary="Still working.",
    )

    events = _events(stream)
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["run_id"] == "run-1" for event in events)
    assert all(event["request_id"] == "req-1" for event in events)
    assert events[0]["metadata"] == {"attempt": 1}
    assert events[0]["occurred_at"].endswith("Z")


def test_progress_summary_is_bounded() -> None:
    stream = io.StringIO()
    sink = StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)

    sink.emit(
        event_type="phase",
        phase="working",
        human_summary="x" * (MAX_PROGRESS_SUMMARY_CHARS + 50),
    )

    summary = _events(stream)[0]["human_summary"]
    assert len(summary) == MAX_PROGRESS_SUMMARY_CHARS
    assert summary.endswith("…")


class _BrokenStream:
    def write(self, _value: str) -> int:
        raise BrokenPipeError("caller closed stdout")

    def flush(self) -> None:
        raise AssertionError("flush must not be reached")


def test_broken_pipe_disables_progress_without_raising() -> None:
    sink = StdoutJsonlProgressSink(
        run_id="run-1",
        request_id="req-1",
        stream=_BrokenStream(),  # type: ignore[arg-type]
    )

    assert (
        sink.emit(
            event_type="start",
            phase="starting",
            human_summary="Starting.",
        )
        is False
    )
    assert sink.enabled is False
    assert (
        sink.emit(
            event_type="phase",
            phase="planning",
            human_summary="Planning.",
        )
        is False
    )


def test_missing_run_id_uses_noop_sink() -> None:
    stream = io.StringIO()
    reporter = progress_reporter_from_input(
        {"request_id": "req-1", "task": "do work"},
        request_id="req-1",
        stream=stream,
    )

    assert reporter.enabled is False
    assert reporter.started() is False
    assert stream.getvalue() == ""


def test_progress_jsonl_input_writes_to_file_not_stdout(tmp_path) -> None:
    progress_path = tmp_path / "progress.jsonl"
    stream = io.StringIO()
    reporter = progress_reporter_from_input(
        {
            "request_id": "req-1",
            "run_id": "run-1",
            "task": "do work",
            "progress_jsonl": str(progress_path),
        },
        request_id="req-1",
        stream=stream,
    )

    assert reporter.enabled is True
    assert reporter.started() is True
    assert stream.getvalue() == ""

    events = [
        json.loads(line) for line in progress_path.read_text().splitlines() if line.strip()
    ]
    assert [event["event_type"] for event in events] == ["start"]
    assert events[0]["run_id"] == "run-1"


def test_workflow_message_translation_does_not_forward_plan_or_rejection_text() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)
    )

    reporter.handle_workflow_message("Plan from coding agent:\nSECRET PLAN BODY")
    reporter.handle_workflow_message("Plan rejected (attempt 2): private reviewer reasoning")
    reporter.handle_workflow_message("[1m 7s] Coding agent running — 42 lines of output so far")

    events = _events(stream)
    payload = json.dumps(events)
    assert "SECRET PLAN BODY" not in payload
    assert "private reviewer reasoning" not in payload
    assert events[0]["human_summary"] == "Implementation plan received."
    assert events[1]["metadata"] == {"attempt": 2}
    assert events[2]["metadata"] == {"elapsed_seconds": 67, "lines_output": 42}


def test_structured_event_adapter_supports_future_deep_agent_events() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)
    )

    assert reporter.emit_structured_event(
        {
            "event_type": "phase",
            "phase": "inspecting_repository",
            "human_summary": "Inspecting the relevant repository files.",
            "metadata": {"source": "deep-agent-custom"},
        }
    )

    event = _events(stream)[0]
    assert event["phase"] == "inspecting_repository"
    assert event["metadata"] == {"source": "deep-agent-custom"}


def test_quiet_work_emits_deterministic_specialist_heartbeat() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream),
        heartbeat_interval_seconds=0.02,
    )
    reporter.phase("analysing", "Analysing the task.")

    with reporter.heartbeat_scope():
        time.sleep(0.09)

    events = _events(stream)
    assert any(event["event_type"] == "heartbeat" for event in events)
    assert all("LLM" not in event["human_summary"] for event in events)


def test_invalid_structured_event_does_not_break_the_workflow() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)
    )

    assert (
        reporter.emit_structured_event(
            {
                "event_type": "raw_provider_event",
                "phase": "unsafe phase!",
                "human_summary": "raw payload",
            }
        )
        is False
    )
    assert reporter.phase("working", "Continuing safely.") is True
    assert [event["human_summary"] for event in _events(stream)] == ["Continuing safely."]


def test_failure_guidance_progress_does_not_forward_coding_agent_output() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)
    )

    emit_terminal_progress(
        reporter,
        {
            "status": "needs_clarification",
            "interrupt_kind": "failure_guidance",
            "summary": "Clarification needed. Last result: SECRET RAW CODING OUTPUT",
        },
    )

    event = _events(stream)[0]
    assert event["human_summary"] == (
        "AI Tech Lead needs corrective guidance after repeated coding-agent failures."
    )
    assert "SECRET RAW CODING OUTPUT" not in stream.getvalue()


def test_terminal_result_statuses_map_to_safe_progress_events() -> None:
    stream = io.StringIO()
    reporter = ProgressReporter(
        StdoutJsonlProgressSink(run_id="run-1", request_id="req-1", stream=stream)
    )

    emit_terminal_progress(
        reporter,
        {"status": "approval_required", "summary": "Approval required: update configuration."},
    )
    emit_terminal_progress(
        reporter,
        {"status": "needs_clarification", "summary": "Clarification needed: choose a project."},
    )
    emit_terminal_progress(
        reporter,
        {"status": "waiting_decision", "summary": "Plan needs guidance (rejected 1x)."},
    )
    emit_terminal_progress(
        reporter,
        {"status": "success", "summary": "Task completed successfully."},
    )
    emit_terminal_progress(
        reporter,
        {"status": "failed", "summary": "Task failed validation."},
    )

    events = _events(stream)
    assert [(event["event_type"], event["phase"]) for event in events] == [
        ("waiting", "waiting_approval"),
        ("waiting", "waiting_clarification"),
        ("waiting", "waiting_decision"),
        ("completed", "completed"),
        ("failure", "failed"),
    ]
    assert events[2]["human_summary"] == "Plan needs guidance (rejected 1x)."
