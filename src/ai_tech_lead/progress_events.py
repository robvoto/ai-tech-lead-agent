"""Transport-neutral specialist progress reporting for Hub subprocess calls.

The public event shape is framework-neutral. The local subprocess adapter writes
one JSON object per line to stdout; normal and debug logs remain on stderr.
The existing final result continues to be written to the caller-provided JSON file.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Protocol, TextIO

from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

PROGRESS_SCHEMA_VERSION = 1
# Product-owned liveness interval for a quiet specialist subprocess. It produces
# deterministic telemetry only and never invokes an LLM.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
MAX_PROGRESS_SUMMARY_CHARS = 280
MAX_PROGRESS_METADATA_STRING_CHARS = 120

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_ELAPSED_RE = re.compile(r"\[(?:(?P<minutes>\d+)m )?(?P<seconds>\d+)s\]")
_LINES_RE = re.compile(r"(?P<lines>\d+) lines")
_ATTEMPT_RE = re.compile(r"attempt (?P<attempt>\d+)", re.IGNORECASE)

_ALLOWED_METADATA_KEYS = {
    "attempt",
    "backend",
    "elapsed_seconds",
    "files_changed",
    "lines_output",
    "source",
    "status",
    "tests_run",
}
_ALLOWED_EVENT_TYPES = {
    "completed",
    "failure",
    "heartbeat",
    "phase",
    "start",
    "waiting",
    "warning",
}


class ProgressSink(Protocol):
    """Minimal output boundary used by workflow and future Deep Agent adapters."""

    @property
    def enabled(self) -> bool: ...

    def emit(
        self,
        *,
        event_type: str,
        phase: str,
        human_summary: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool: ...


class NullProgressSink:
    """No-op sink used when the caller did not request live progress."""

    @property
    def enabled(self) -> bool:
        return False

    def emit(
        self,
        *,
        event_type: str,
        phase: str,
        human_summary: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        del event_type, phase, human_summary, metadata
        return False


class StdoutJsonlProgressSink:
    """Write validated progress events to reserved stdout as JSONL."""

    def __init__(
        self,
        *,
        run_id: str,
        request_id: str,
        stream: TextIO | None = None,
    ) -> None:
        self._run_id = _required_identifier(run_id, field="run_id")
        self._request_id = _required_identifier(request_id, field="request_id")
        self._stream = stream or sys.stdout
        self._sequence = 0
        self._lock = threading.Lock()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def emit(
        self,
        *,
        event_type: str,
        phase: str,
        human_summary: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        if not self._enabled:
            return False

        normalized_event = _validated_event_type(event_type)
        normalized_phase = _validated_phase(phase)
        normalized_summary = _bounded_summary(human_summary)
        normalized_metadata = _bounded_metadata(metadata)

        with self._lock:
            if not self._enabled:
                return False
            self._sequence += 1
            payload = {
                "schema_version": PROGRESS_SCHEMA_VERSION,
                "run_id": self._run_id,
                "request_id": self._request_id,
                "sequence": self._sequence,
                "event_type": normalized_event,
                "phase": normalized_phase,
                "human_summary": normalized_summary,
                "occurred_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "metadata": normalized_metadata,
            }
            try:
                self._stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                self._stream.write("\n")
                self._stream.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._enabled = False
                logger.warning(
                    "[SUBPROCESS][PROGRESS] Progress stream disabled after write failure: %s",
                    exc,
                )
                return False
        return True


class ProgressReporter:
    """Friendly workflow-facing progress API with deterministic quiet heartbeats."""

    def __init__(
        self,
        sink: ProgressSink | None = None,
        *,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._sink = sink or NullProgressSink()
        self._heartbeat_interval_seconds = max(0.0, float(heartbeat_interval_seconds))
        self._state_lock = threading.Lock()
        self._current_phase = "starting"
        self._last_emit_monotonic = time.monotonic()

    @property
    def enabled(self) -> bool:
        return self._sink.enabled

    def emit(
        self,
        *,
        event_type: str,
        phase: str,
        human_summary: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        try:
            emitted = self._sink.emit(
                event_type=event_type,
                phase=phase,
                human_summary=human_summary,
                metadata=metadata,
            )
        except Exception:
            logger.exception(
                "[SUBPROCESS][PROGRESS] Progress event rejected event_type=%s phase=%s",
                event_type,
                phase,
            )
            return False
        if emitted:
            with self._state_lock:
                self._current_phase = phase
                self._last_emit_monotonic = time.monotonic()
        return emitted

    def started(self) -> bool:
        return self.emit(
            event_type="start",
            phase="starting",
            human_summary="AI Tech Lead accepted the task.",
        )

    def phase(self, phase: str, summary: str, metadata: Mapping[str, Any] | None = None) -> bool:
        return self.emit(
            event_type="phase",
            phase=phase,
            human_summary=summary,
            metadata=metadata,
        )

    def waiting(self, phase: str, summary: str) -> bool:
        return self.emit(event_type="waiting", phase=phase, human_summary=summary)

    def completed(self, summary: str) -> bool:
        return self.emit(event_type="completed", phase="completed", human_summary=summary)

    def failed(self, summary: str) -> bool:
        return self.emit(event_type="failure", phase="failed", human_summary=summary)

    def cancelled(self) -> bool:
        return self.emit(
            event_type="warning",
            phase="cancelled",
            human_summary="AI Tech Lead run was cancelled.",
        )

    def handle_workflow_message(self, message: str) -> bool:
        """Translate existing graph/coding-runner callbacks into safe stable phases."""

        normalized = " ".join(str(message).split())
        if not normalized:
            return False

        lower = normalized.lower()
        attempt = _extract_attempt(normalized)
        metadata: dict[str, Any] = {}
        if attempt is not None:
            metadata["attempt"] = attempt

        if lower.startswith("requesting implementation plan") or lower.startswith(
            "requesting revised plan"
        ):
            return self.phase("planning", "Preparing an implementation plan.", metadata)
        if lower.startswith("plan from coding agent:"):
            return self.phase("planning", "Implementation plan received.")
        if lower.startswith("coding agent returned an empty plan"):
            return self.emit(
                event_type="warning",
                phase="planning",
                human_summary="Coding agent returned an empty implementation plan.",
            )
        if lower.startswith("plan received. reviewing"):
            return self.phase("reviewing_plan", "Reviewing the implementation plan.")
        if lower.startswith("plan rejected"):
            return self.emit(
                event_type="warning",
                phase="revising_plan",
                human_summary="Implementation plan needs revision.",
                metadata=metadata,
            )
        if lower.startswith("plan approved"):
            return self.phase(
                "preparing_implementation", "Plan approved. Preparing implementation."
            )
        if lower.startswith("running coding agent"):
            return self.phase("coding", "Coding agent started.")
        if "coding agent running" in lower or (lower.startswith("[") and "still running" in lower):
            runtime_metadata = _runtime_metadata(normalized)
            return self.emit(
                event_type="heartbeat",
                phase="coding",
                human_summary="Coding agent is still running.",
                metadata=runtime_metadata,
            )

        logger.debug("[SUBPROCESS][PROGRESS] Ignored unrecognised workflow update: %s", normalized)
        return False

    def emit_structured_event(self, event: Mapping[str, Any]) -> bool:
        """Adapter hook for future LangGraph/Deep Agent custom events.

        The caller must provide an already human-safe summary; raw model/tool events
        must be translated before reaching this boundary.
        """

        event_type = str(event.get("event_type", "")).strip()
        phase = str(event.get("phase", "")).strip()
        summary = str(event.get("human_summary", "")).strip()
        metadata = event.get("metadata")
        return self.emit(
            event_type=event_type,
            phase=phase,
            human_summary=summary,
            metadata=metadata if isinstance(metadata, Mapping) else None,
        )

    @contextmanager
    def heartbeat_scope(self):
        """Emit specialist liveness only after a genuinely quiet interval."""

        if not self.enabled or self._heartbeat_interval_seconds <= 0:
            yield
            return

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(stop_event,),
            name="ai-tech-lead-progress-heartbeat",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=max(1.0, self._heartbeat_interval_seconds))

    def _heartbeat_loop(self, stop_event: threading.Event) -> None:
        check_interval = min(1.0, max(0.05, self._heartbeat_interval_seconds / 4))
        while not stop_event.wait(check_interval):
            with self._state_lock:
                silence = time.monotonic() - self._last_emit_monotonic
                phase = self._current_phase
            if silence < self._heartbeat_interval_seconds:
                continue
            self.emit(
                event_type="heartbeat",
                phase=phase,
                human_summary=f"AI Tech Lead is still working: {phase.replace('_', ' ')}.",
            )


def progress_reporter_from_input(
    task_input: Mapping[str, Any],
    *,
    request_id: str,
    stream: TextIO | None = None,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> ProgressReporter:
    """Create stdout progress only when a caller supplies a valid run_id."""

    run_id = task_input.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return ProgressReporter()
    try:
        sink = StdoutJsonlProgressSink(
            run_id=run_id,
            request_id=request_id,
            stream=stream,
        )
    except ValueError as exc:
        logger.warning("[SUBPROCESS][PROGRESS] Live progress disabled: %s", exc)
        return ProgressReporter()
    return ProgressReporter(
        sink,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


def emit_terminal_progress(reporter: ProgressReporter, result: Mapping[str, Any]) -> None:
    """Map the existing final result status to one terminal progress event."""

    status = str(result.get("status", "failed"))
    summary = _bounded_summary(str(result.get("summary", "AI Tech Lead finished.")))
    if status == "success":
        result_kind = str(result.get("result_kind", ""))
        completed_summary = (
            "AI Tech Lead completed the task."
            if result_kind == "execution_result"
            else "AI Tech Lead prepared the coding instruction."
        )
        reporter.completed(completed_summary)
    elif status == "approval_required":
        reporter.waiting("waiting_approval", summary)
    elif status == "needs_clarification":
        interrupt_kind = str(result.get("interrupt_kind", ""))
        if interrupt_kind == "failure_guidance":
            summary = "AI Tech Lead needs corrective guidance after repeated coding-agent failures."
        elif interrupt_kind == "plan_guidance":
            summary = "AI Tech Lead needs human guidance for the implementation plan."
        reporter.waiting("waiting_clarification", summary)
    else:
        reporter.failed("AI Tech Lead failed. Check the final result for details.")


def _required_identifier(value: str, *, field: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized or len(normalized) > 80:
        raise ValueError(f"{field} must be a non-empty string of at most 80 characters")
    return normalized


def _validated_event_type(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in _ALLOWED_EVENT_TYPES or _EVENT_TYPE_RE.fullmatch(normalized) is None:
        raise ValueError(f"unsupported progress event_type: {value!r}")
    return normalized


def _validated_phase(value: str) -> str:
    normalized = str(value).strip().lower().replace(" ", "_")
    if _PHASE_RE.fullmatch(normalized) is None:
        raise ValueError(f"invalid progress phase: {value!r}")
    return normalized


def _bounded_summary(value: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError("progress human_summary must not be empty")
    if len(normalized) <= MAX_PROGRESS_SUMMARY_CHARS:
        return normalized
    return normalized[: MAX_PROGRESS_SUMMARY_CHARS - 1].rstrip() + "…"


def _bounded_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        if isinstance(value, bool) or value is None:
            normalized[key] = value
        elif isinstance(value, int | float):
            normalized[key] = value
        elif isinstance(value, str):
            normalized[key] = " ".join(value.split())[:MAX_PROGRESS_METADATA_STRING_CHARS]
    return normalized


def _extract_attempt(message: str) -> int | None:
    match = _ATTEMPT_RE.search(message)
    return int(match.group("attempt")) if match else None


def _runtime_metadata(message: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    elapsed_match = _ELAPSED_RE.search(message)
    if elapsed_match:
        minutes = int(elapsed_match.group("minutes") or 0)
        seconds = int(elapsed_match.group("seconds"))
        metadata["elapsed_seconds"] = minutes * 60 + seconds
    lines_match = _LINES_RE.search(message)
    if lines_match:
        metadata["lines_output"] = int(lines_match.group("lines"))
    return metadata
