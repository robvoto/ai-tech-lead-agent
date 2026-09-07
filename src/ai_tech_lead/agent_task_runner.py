"""Subprocess-facing entry point: receive a task via JSON, return structured JSON output.

Called through the local JSON subprocess contract. No Telegram. No admin UI.

Security contract
-----------------
* project_root must be supplied explicitly for target-project work and must either
  match a settings.project_registry entry or arrive with explicit human approval.
  Callers cannot inject arbitrary filesystem paths by default.
* Execution (running Codex / Claude Code) is gated on settings.execute_coding_agent.
  The caller's execution_mode is a *request*, not a grant.
  Unknown execution_mode values fail closed.
* requires_human_approval from the caller is ignored for the execution decision —
  that is the risk reviewer graph node's job.

Resumable pauses
-----------------
When the workflow pauses (approval, guidance after a plan rejection or coding
failure, or an online-research approval), the response reports a `pending_decision`
block: a plain-language prompt plus the named options available right now, some of
which need extra text. The paused conversation is kept durably (the same mechanism
Telegram's bot already relies on), keyed on `request_id`. To act on it, resubmit
`request_id` plus a `decision: {"option": ..., "text": ..., "actor": ...}` — no
`task` field is needed on that call, and the actual paused conversation resumes
rather than the workflow restarting from scratch. `pending_decision` is the only
place option names are declared; nothing here hardcodes what a caller is allowed to
send beyond "one of the options this response just listed."
"""

from __future__ import annotations

import json
import logging
import os
import traceback
import uuid
from pathlib import Path
from typing import Any

from langgraph.types import Command

from .agent_manifest import agent_manifest_reference
from .app_settings import load_settings
from .backlog_reference import BacklogReferenceError, resolve_backlog_reference
from .backlog_refinement_capability import (
    BacklogRefinementProposal,
    append_approved_backlog_refinement,
    build_backlog_refinement_block_summary,
    format_backlog_refinement_review,
    infer_backlog_item_prefix,
    prepare_backlog_refinement_proposal,
    render_backlog_refinement_draft_text,
)
from .backlog_refinement_store import BacklogRefinementStore
from .backlog_repository import BacklogValidationError
from .backlog_runtime_store import BacklogRuntimeStore, enqueue_and_flush_update
from .backlog_sheets_repository import (
    BacklogSheetLayout,
    BacklogSourceUnavailableError,
    SheetsBacklogRepository,
    repository_for,
)
from .checkpointer_store import get_checkpointer
from .logging_setup import LOGGER_NAME
from .progress_events import (
    ProgressReporter,
    emit_terminal_progress,
    progress_reporter_from_input,
)
from .run_audit_store import record_run_audit_summary
from .runtime_lock import RuntimeLockBusyError, acquire_request_run_lock
from .target_project_context import (
    BacklogColumnContext,
    BacklogItemContext,
    BacklogProjectContext,
    ResourceReferenceContext,
    TargetProjectContext,
    resolve_universal_project_context,
)

logger = logging.getLogger(LOGGER_NAME)

STATUS_SUCCESS = "success"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_WAITING_DECISION = "waiting_decision"
STATUS_FAILED = "failed"
ALLOWED_EXECUTION_MODES = {"instruction_only", "execute"}

RESULT_KIND_INSTRUCTION_PACKAGE = "instruction_package"
RESULT_KIND_EXECUTION_RESULT = "execution_result"
RESULT_KIND_CLARIFICATION_REQUEST = "clarification_request"
RESULT_KIND_DECISION_REQUIRED = "decision_required"
RESULT_KIND_TERMINAL_FAILURE = "terminal_failure"
RESULT_KIND_BACKLOG_REFINEMENT_DRAFT = "backlog_refinement_draft"
RESULT_KIND_BACKLOG_ITEM_CREATED = "backlog_item_created"
ALLOWED_TASK_KINDS = {"coding_task", "technical_analysis", "backlog_refinement"}

# One entry per LangGraph interrupt `kind` this runner knows how to resume, and the
# options that pause offers. This table is the single source of truth for both what
# gets reported to the caller and what a resumed decision is allowed to say.
_DECISION_OPTIONS_BY_KIND: dict[str, list[dict[str, Any]]] = {
    "approval": [
        {"name": "approve"},
        {"name": "request_changes", "needs_text": True},
        {"name": "ask_question", "needs_text": True},
        {"name": "cancel"},
    ],
    "plan_guidance": [{"name": "answer", "needs_text": True}],
    "failure_guidance": [{"name": "answer", "needs_text": True}],
    "research_approval": [{"name": "approve"}, {"name": "cancel"}],
    "context_clarification": [{"name": "answer", "needs_text": True}],
    "validation_command": [{"name": "answer", "needs_text": True}, {"name": "skip"}],
    "project_guidance_governance": [{"name": "approve"}, {"name": "reject"}],
    "completion_verification": [
        {"name": "confirm_complete"},
        {"name": "reject", "needs_text": True},
    ],
    "integration_approval": [
        {"name": "approve"},
        {"name": "cancel"},
    ],
}


def run_agent_task(input_path: str | Path, output_path: str | Path) -> int:
    """Read a task from input_path, run the workflow, write result to output_path.

    Returns 0 on success or soft outcomes (needs_clarification, waiting_decision).
    Returns 1 on hard failures.
    """
    input_file = Path(input_path)
    output_file = Path(output_path)

    try:
        task_input = json.loads(input_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _write_output(
            output_file,
            _error_response("", f"Could not read input file: {exc}"),
            settings=None,
        )
        return 1

    request_id = task_input.get("request_id") or str(uuid.uuid4())
    task_text = task_input.get("task", "").strip()
    decision = _parse_decision(task_input.get("decision"))
    progress_reporter = progress_reporter_from_input(task_input, request_id=request_id)

    if decision is not None and not task_input.get("request_id"):
        _write_error_output(
            output_file,
            progress_reporter,
            request_id,
            "request_id is required to resume a paused conversation with a decision.",
            settings=None,
        )
        return 1

    if not task_text and decision is None:
        _write_error_output(
            output_file,
            progress_reporter,
            request_id,
            "task field is required and must not be empty (unless resuming with a decision).",
            settings=None,
        )
        return 1

    # Load local settings — security gates are server-side, not caller-controlled.
    try:
        settings = load_settings()
    except Exception as exc:
        _write_error_output(
            output_file,
            progress_reporter,
            request_id,
            f"Failed to load settings: {exc}",
            settings=None,
        )
        return 1

    # Execution gate: local settings decide, not the caller.
    # The caller can *request* execution_mode=execute, but settings.execute_coding_agent
    # must also be True. The caller's requires_human_approval is intentionally ignored.
    # Decision-only resumes may omit request metadata. Recover it from the
    # persisted subprocess checkpoint before applying new-request defaults.
    resume_metadata = (
        _request_checkpoint_metadata(request_id)
        if decision is not None
        and (task_input.get("task_kind") is None or task_input.get("execution_mode") is None)
        else {}
    )
    task_kind_raw = task_input.get("task_kind")
    if task_kind_raw is None:
        task_kind_raw = resume_metadata.get("task_kind")
    task_kind = _validate_task_kind(task_kind_raw)
    if task_kind is None:
        _write_error_output(
            output_file,
            progress_reporter,
            request_id,
            "task_kind must be one of: coding_task, technical_analysis, backlog_refinement.",
            settings=settings,
        )
        return 1
    execution_mode_raw = task_input.get("execution_mode")
    if execution_mode_raw is None:
        execution_mode_raw = resume_metadata.get("execution_mode")
    execution_mode = _validate_execution_mode(execution_mode_raw)
    if execution_mode is None:
        _write_error_output(
            output_file,
            progress_reporter,
            request_id,
            "execution_mode must be one of: instruction_only, execute.",
            settings=settings,
        )
        return 1
    execute_coding_agent = execution_mode == "execute" and settings.execute_coding_agent

    try:
        request_lock = acquire_request_run_lock(request_id)
    except RuntimeLockBusyError as exc:
        _write_error_output(
            output_file,
            progress_reporter,
            request_id,
            str(exc),
            settings=settings,
        )
        return 1

    try:
        # backlog_project (project_reference.backlog) is resolved first, and unlike
        # backlog_reference it never triggers Sheets work by itself — this is a safe
        # place to fail closed before anything else has happened.
        try:
            backlog_project = _resolve_backlog_project(task_input)
        except ValueError as exc:
            _write_error_output(output_file, progress_reporter, request_id, str(exc), settings=settings)
            return 1

        # Backlog reference: an explicit, caller-supplied pointer to one row in one
        # Google Sheet. Resolved and snapshotted before execution; never a hidden
        # or discovered selection. No reference means no backlog side-effects.
        backlog_reference = None
        sheets_repository = None
        source_record = None
        raw_backlog_reference = task_input.get("backlog_reference")
        if raw_backlog_reference is not None:
            try:
                backlog_reference = resolve_backlog_reference(raw_backlog_reference, settings)
            except BacklogReferenceError as exc:
                _write_error_output(
                    output_file,
                    progress_reporter,
                    request_id,
                    f"backlog_reference could not be resolved: {exc}",
                    settings=settings,
                )
                return 1

            if backlog_project is not None and (
                backlog_project.spreadsheet_id != backlog_reference.spreadsheet_id
                or backlog_project.sheet_name != backlog_reference.sheet_name
            ):
                _write_error_output(
                    output_file,
                    progress_reporter,
                    request_id,
                    "backlog_reference and project_reference.backlog identify different "
                    f"backlog resources: backlog_reference points to spreadsheet "
                    f"'{backlog_reference.spreadsheet_id}' sheet '{backlog_reference.sheet_name}', "
                    f"but project_reference.backlog points to spreadsheet "
                    f"'{backlog_project.spreadsheet_id}' sheet '{backlog_project.sheet_name}'. "
                    "Supply matching resources, or only one of the two.",
                    settings=settings,
                )
                return 1

            # Once validated as the same resource (or backlog_project is absent), the
            # two paths must agree on column layout too — never silently pick one.
            layout = (
                BacklogSheetLayout(columns=backlog_project.columns)
                if backlog_project is not None
                else None
            )
            try:
                sheets_repository = SheetsBacklogRepository(
                    backlog_reference,
                    credentials_path=settings.backlog_google_credentials_path,
                    layout=layout,
                )
                source_record = sheets_repository.get_item_with_source(backlog_reference.item_id)
            except (ValueError, BacklogValidationError, BacklogSourceUnavailableError) as exc:
                _write_error_output(
                    output_file,
                    progress_reporter,
                    request_id,
                    f"backlog_reference could not be resolved: {exc}",
                    settings=settings,
                )
                return 1
            BacklogRuntimeStore().save_snapshot(
                request_id=request_id,
                project_key=backlog_reference.project_key,
                spreadsheet_id=backlog_reference.spreadsheet_id,
                sheet_name=backlog_reference.sheet_name,
                item_id=backlog_reference.item_id,
                row_data=source_record.row_values,
                row_hash=source_record.row_hash,
                fetched_at=source_record.fetched_at,
            )

        try:
            target_project_context = _build_target_project_context(
                task_input=task_input,
                settings=settings,
                backlog_reference=backlog_reference,
                source_record=source_record,
                backlog_project=backlog_project,
            )
        except ValueError as exc:
            _write_error_output(
                output_file,
                progress_reporter,
                request_id,
                str(exc),
                settings=settings,
            )
            return 1

        progress_reporter.started()
        logger.info(
            "[SUBPROCESS] run-agent-task request_id=%s kind=%s decision=%s task=%s...",
            request_id,
            task_kind,
            decision.option if decision else "(none)",
            task_text[:80],
        )

        try:
            with progress_reporter.heartbeat_scope():
                if task_kind == "backlog_refinement":
                    result = _run_backlog_refinement_task(
                        request_id=request_id,
                        task=task_text,
                        decision=decision,
                        progress_reporter=progress_reporter,
                        target_project_context=target_project_context,
                        settings=settings,
                    )
                    # No graph runs for refinement, so nothing can resolve a backlog
                    # item beyond what the caller already supplied up front.
                    final_target_project_context = target_project_context
                else:
                    result, final_target_project_context = _execute_workflow(
                        request_id=request_id,
                        task=task_text,
                        task_kind=task_kind,
                        execution_mode=execution_mode,
                        execute_coding_agent=execute_coding_agent,
                        decision=decision,
                        progress_reporter=progress_reporter,
                        target_project_context=target_project_context,
                        settings=settings,
                    )
        except (KeyboardInterrupt, SystemExit):
            progress_reporter.cancelled()
            raise
        except _DecisionRejected as exc:
            _write_error_output(
                output_file,
                progress_reporter,
                request_id,
                str(exc),
                settings=settings,
            )
            return 1
        except Exception as exc:
            logger.exception("[SUBPROCESS] Unexpected error running workflow")
            _write_error_output(
                output_file,
                progress_reporter,
                request_id,
                f"Unexpected error: {exc}",
                detail=traceback.format_exc(),
                settings=settings,
            )
            return 1

        if (
            final_target_project_context is not None
            and final_target_project_context.backlog_item is not None
        ):
            result["backlog_sync_status"] = _sync_backlog_completion(
                request_id=request_id,
                target_project_context=final_target_project_context,
                result=result,
                settings=settings,
            )

        emit_terminal_progress(progress_reporter, result)
        _write_output(output_file, result, settings=settings)
        return (
            0
            if result.get("status")
            in (STATUS_SUCCESS, STATUS_NEEDS_CLARIFICATION, STATUS_WAITING_DECISION)
            else 1
        )
    finally:
        request_lock.release()


class _DecisionRejected(ValueError):
    """A resume decision didn't match what the paused conversation is offering."""


class _Decision:
    __slots__ = ("option", "text", "actor")

    def __init__(self, option: str, text: str, actor: str) -> None:
        self.option = option
        self.text = text
        self.actor = actor


def _parse_decision(raw_decision: Any) -> _Decision | None:
    """Parse the caller-supplied decision, or return None if absent."""

    if raw_decision is None:
        return None
    if not isinstance(raw_decision, dict):
        raise _DecisionRejected("decision must be a JSON object with at least an 'option' field.")
    option = str(raw_decision.get("option", "")).strip()
    if not option:
        raise _DecisionRejected("decision.option is required.")
    text = str(raw_decision.get("text", "")).strip()
    actor = str(raw_decision.get("actor", "")).strip()
    return _Decision(option=option, text=text, actor=actor)


def _merge_resource_references(
    task_input: dict[str, Any], wire_references: list[str]
) -> list[dict[str, Any]]:
    """Fold Hub's universal references into the internal resource_references shape.

    `wire_references` is already resolved (from either the versioned `project_context`
    envelope or legacy flat `references`) by `resolve_universal_project_context` — an
    uninterpreted list of pointer strings (ticket IDs, file paths, URLs, ...); Hub does
    not know or guess what they mean. Legacy callers already supply `resource_references`
    as dicts with an id. Both are merged into the dict shape `resolve_request_context`
    (request_context.py) understands, without resolving or interpreting either — that
    stays request_context's job alone.
    """
    merged: dict[str, dict[str, Any]] = {}
    for reference in task_input.get("resource_references") or []:
        if isinstance(reference, dict):
            ref_id = str(reference.get("item_id") or reference.get("id") or "").strip()
            if ref_id:
                merged[ref_id] = reference
    for reference in wire_references:
        ref_id = str(reference).strip()
        if ref_id and ref_id not in merged:
            merged[ref_id] = {"id": ref_id}
    return list(merged.values())


def _resolve_backlog_project(task_input: dict[str, Any]) -> BacklogProjectContext | None:
    """Parse project_reference.backlog (or legacy backlog_project) up front.

    Resolved once, early, so it can be checked against an explicit
    backlog_reference for the same resource before any Sheets work starts —
    and reused unchanged for the initial fetch layout, _build_target_project_context,
    and (later) completion sync, instead of three independent derivations.
    """
    raw_project_reference = task_input.get("project_reference")
    if raw_project_reference is None:
        return None
    if not isinstance(raw_project_reference, dict):
        raise ValueError("project_reference must be a JSON object when supplied.")
    raw_backlog_project = raw_project_reference.get("backlog")
    if raw_backlog_project is None:
        raw_backlog_project = raw_project_reference.get("backlog_project")
    return BacklogProjectContext.from_payload(raw_backlog_project)


def _build_target_project_context(
    *,
    task_input: dict[str, Any],
    settings: Any,
    backlog_reference: Any | None,
    source_record: Any | None,
    backlog_project: BacklogProjectContext | None,
) -> TargetProjectContext:
    raw_project_reference = task_input.get("project_reference")
    if raw_project_reference is None:
        project_reference: dict[str, Any] = {}
    elif isinstance(raw_project_reference, dict):
        project_reference = raw_project_reference
    else:
        raise ValueError("project_reference must be a JSON object when supplied.")

    wire_project_root, wire_references = resolve_universal_project_context(task_input)

    nested_project_root = project_reference.get("project_root")
    if wire_project_root is not None and nested_project_root is not None:
        wire_resolved = str(Path(str(wire_project_root)).resolve())
        nested_resolved = str(Path(str(nested_project_root)).resolve())
        if wire_resolved != nested_resolved:
            raise ValueError(
                "project_root does not match project_reference.project_root. "
                "Supply only one target project root, or make them identical."
            )

    requested_project_root = wire_project_root
    if requested_project_root is None:
        requested_project_root = nested_project_root

    if requested_project_root is not None:
        project_root = _validate_project_root(
            requested_project_root,
            settings,
            human_approved=bool(task_input.get("human_approved")),
        )
    else:
        project_root = None

    backlog_item = None
    if backlog_reference is not None and source_record is not None:
        backlog_item = BacklogItemContext(
            project_key=backlog_reference.project_key,
            spreadsheet_id=backlog_reference.spreadsheet_id,
            sheet_name=backlog_reference.sheet_name,
            item_id=backlog_reference.item_id,
            title=source_record.item.title,
            body=source_record.item.body,
            row_hash=source_record.row_hash,
            fetched_at=source_record.fetched_at,
            priority=source_record.item.priority,
            size=source_record.item.complexity,
        )

    return TargetProjectContext(
        project_root=project_root or "",
        project_key=str(project_reference.get("project_key", "")).strip(),
        project_name=str(project_reference.get("project_name", "")).strip(),
        backlog_project=backlog_project,
        resource_references=tuple(
            ResourceReferenceContext(
                item_id=str(reference.get("item_id") or reference.get("id") or "").strip(),
                title=str(reference.get("title", "")).strip(),
            )
            for reference in _merge_resource_references(task_input, wire_references)
            if str(reference.get("item_id") or reference.get("id") or "").strip()
        ),
        backlog_item=backlog_item,
    )


def _validate_project_root(
    project_root_raw: str | None,
    settings: Any,
    *,
    human_approved: bool = False,
) -> str | None:
    """Return the resolved project_root when the caller is authorised to use it.

    If project_root_raw is None, returns None. Registered projects must also have an
    available location, supported platform, and required credentials. Explicit human
    approval can authorise one unregistered root, but it does not bypass availability
    checks.
    """
    if project_root_raw is None:
        return None
    requested = str(Path(project_root_raw).resolve())
    registry_entry = settings.project_registry_entry_for_root(requested)

    if registry_entry is None:
        if human_approved:
            if not Path(requested).is_dir():
                raise ValueError(
                    f"project_root '{requested}' was explicitly approved, but its location "
                    "is unavailable or not a directory."
                )
            return requested
        raise ValueError(
            f"project_root '{requested}' is not registered in AI Tech Lead's authorised "
            "project_registry and has not been explicitly approved. Register the target "
            "location or resume the task with explicit human approval."
        )

    if registry_entry.platform != "filesystem":
        raise ValueError(
            f"project_root '{requested}' is registered for platform "
            f"'{registry_entry.platform}', but AI Tech Lead only has an authorised "
            "filesystem location for this task. Provide an available project_root for "
            "that project or register a supported location."
        )

    missing_credentials = [
        name for name in registry_entry.required_credentials_env if not os.environ.get(name)
    ]
    if missing_credentials:
        raise ValueError(
            f"project_root '{requested}' is registered, but authorised access is unavailable "
            f"because required credentials are missing: {', '.join(sorted(missing_credentials))}."
        )

    if not Path(requested).is_dir():
        raise ValueError(
            f"project_root '{requested}' is registered, but its location is unavailable or "
            "not a directory."
        )

    return requested


def _validate_execution_mode(execution_mode_raw: Any) -> str | None:
    """Return a normalized execution_mode, or None if the caller supplied an invalid value."""

    if execution_mode_raw is None:
        return "instruction_only"
    if not isinstance(execution_mode_raw, str):
        return None

    execution_mode = execution_mode_raw.strip()
    if execution_mode in ALLOWED_EXECUTION_MODES:
        return execution_mode
    return None


def _request_checkpoint_metadata(request_id: str) -> dict[str, Any]:
    """Return persisted subprocess metadata for a request, if checkpointed."""

    checkpoint = get_checkpointer().get_tuple(
        {"configurable": {"thread_id": f"subprocess-{request_id}"}}
    )
    metadata = checkpoint.metadata if checkpoint is not None else None
    return dict(metadata) if isinstance(metadata, dict) else {}


def _validate_task_kind(task_kind_raw: Any) -> str | None:
    if task_kind_raw is None:
        return "coding_task"
    if not isinstance(task_kind_raw, str):
        return None
    task_kind = task_kind_raw.strip()
    if task_kind in ALLOWED_TASK_KINDS:
        return task_kind
    return None


def _run_backlog_refinement_task(
    *,
    request_id: str,
    task: str,
    decision: _Decision | None,
    progress_reporter: ProgressReporter,
    target_project_context: TargetProjectContext,
    settings: Any,
) -> dict[str, Any]:
    """Run the shared backlog-refinement capability for Hub callers."""

    reporter = progress_reporter or ProgressReporter()
    reporter.phase("analysing", "Preparing backlog refinement.")
    repository, item_id_prefix = _backlog_refinement_repository(
        target_project_context=target_project_context,
        settings=settings,
    )
    store = BacklogRefinementStore()

    if decision is not None:
        pending = store.get_pending(request_id)
        if pending is None:
            raise _DecisionRejected(
                f"No pending backlog refinement found for request_id={request_id}. "
                "It may have already been resolved, or never created."
            )
        if decision.option not in {"approve", "cancel"}:
            raise _DecisionRejected(
                f"'{decision.option}' is not a valid option for a pending backlog refinement "
                "decision. Valid options: approve, cancel."
            )
        store.clear_pending(request_id)
        if decision.option == "cancel":
            reporter.phase("finalising", "Backlog refinement was rejected.")
            return _backlog_refinement_output(
                request_id=request_id,
                status=STATUS_NEEDS_CLARIFICATION,
                summary="Backlog refinement rejected. No backlog item was written.",
                next_action="Submit a revised backlog refinement request if you still want this work.",
                result_kind=RESULT_KIND_CLARIFICATION_REQUEST,
                backlog_refinement=pending.proposal,
                pending_decision=None,
            )

        reporter.phase("finalising", "Writing approved backlog refinement to the backlog.")
        item = append_approved_backlog_refinement(pending.proposal, repository)
        summary = f"Backlog item created: {item.item_id} - {item.title}"
        return _backlog_refinement_output(
            request_id=request_id,
            status=STATUS_SUCCESS,
            summary=summary,
            next_action="Review the created backlog item.",
            result_kind=RESULT_KIND_BACKLOG_ITEM_CREATED,
            backlog_refinement=pending.proposal,
            pending_decision=None,
        )

    proposal = prepare_backlog_refinement_proposal(
        text=task,
        repository=repository,
        settings=settings,
        item_id_prefix=item_id_prefix,
    )
    if proposal.blocked:
        reporter.phase("finalising", "Backlog refinement blocked by matching backlog work.")
        return _backlog_refinement_output(
            request_id=request_id,
            status=STATUS_NEEDS_CLARIFICATION,
            summary=build_backlog_refinement_block_summary(proposal),
            next_action="Review the matching backlog items and submit a narrower or revised request.",
            result_kind=RESULT_KIND_CLARIFICATION_REQUEST,
            backlog_refinement=proposal,
            pending_decision=None,
        )

    store.save_pending(request_id=request_id, proposal=proposal)
    reporter.phase("finalising", "Backlog refinement ready for approval.")
    return _backlog_refinement_output(
        request_id=request_id,
        status=STATUS_WAITING_DECISION,
        summary=f"Backlog refinement ready: {proposal.draft.item_id} - {proposal.draft.title}",
        next_action="Resubmit request_id with a decision. Options: approve, cancel.",
        result_kind=RESULT_KIND_BACKLOG_REFINEMENT_DRAFT,
        backlog_refinement=proposal,
        pending_decision={
            "thread_id": f"backlog-refinement-{request_id}",
            "kind": "backlog_refinement_approval",
            "prompt": format_backlog_refinement_review(proposal),
            "options": [{"name": "approve"}, {"name": "cancel"}],
        },
    )


def _backlog_refinement_repository(
    *,
    target_project_context: TargetProjectContext,
    settings: Any,
) -> tuple[SheetsBacklogRepository, str]:
    backlog_project = target_project_context.require_backlog_project("Hub backlog refinement")
    layout = BacklogSheetLayout(columns=backlog_project.columns)
    repository = repository_for(
        backlog_project.spreadsheet_id,
        backlog_project.sheet_name,
        credentials_path=settings.backlog_google_credentials_path,
        layout=layout,
    )
    item_id_prefix = backlog_project.item_id_prefix or infer_backlog_item_prefix(repository)
    return repository, item_id_prefix


def _execute_workflow(
    *,
    request_id: str,
    task: str,
    execute_coding_agent: bool,
    task_kind: str,
    execution_mode: str,
    decision: _Decision | None = None,
    progress_reporter: ProgressReporter | None = None,
    target_project_context: TargetProjectContext | None = None,
    settings: Any | None = None,
) -> tuple[dict[str, Any], TargetProjectContext | None]:
    """Run the graph and return (output, final target_project_context).

    The final context may carry a `backlog_item` the graph itself resolved
    mid-run (the known-backlog-resolution path in coding_workflow_graph.py),
    not just one supplied up front — callers doing completion sync must read
    it from here, not from the pre-graph `target_project_context` argument.
    The required request kind and execution mode are stored in checkpoint metadata
    so decision-only resumes retain the original request semantics.
    """
    from .coding_workflow_graph import build_graph, build_initial_graph_state

    reporter = progress_reporter or ProgressReporter()
    reporter.phase("analysing", "Analysing the task and project context.")
    project_root_override = (
        target_project_context.project_root
        if target_project_context is not None and target_project_context.project_root
        else None
    )
    graph = build_graph(
        checkpointer_storage=get_checkpointer(),
        execute_coding_agent_override=execute_coding_agent,
        project_root_override=project_root_override,
        coding_agent_progress_callback=(
            reporter.handle_workflow_message if reporter.enabled else None
        ),
        workflow_progress_callback=(
            (lambda phase, summary: reporter.phase(phase, summary))
            if reporter.enabled
            else None
        ),
    )

    thread_id = f"subprocess-{request_id}"
    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": {
            "task_kind": task_kind,
            "execution_mode": execution_mode,
        },
    }

    if decision is not None:
        pending_snapshot = graph.get_state(config)
        pending_interrupt = _pending_interrupt_value(pending_snapshot)
        pending_kind = (
            str(pending_interrupt.get("kind", "")).strip()
            if isinstance(pending_interrupt, dict)
            else ""
        )
        if not pending_kind or pending_kind not in _DECISION_OPTIONS_BY_KIND:
            raise _DecisionRejected(
                f"No paused decision found for request_id={request_id}. "
                "It may have already been resolved, or never paused."
            )
        resume_payload = _map_decision_to_resume_payload(pending_kind, decision)
        final_state = graph.invoke(Command(resume=resume_payload), config=config)
    else:
        initial_state: dict[str, Any] = build_initial_graph_state(
            task,
            request_id=request_id,
            force_approval=False,
            target_project_context=target_project_context,
        )
        if settings is not None:
            initial_state.update(
                {
                    "orchestrator_run_max_calls": settings.orchestrator_run_max_calls,
                    "orchestrator_run_max_tokens": settings.orchestrator_run_max_tokens,
                    "orchestrator_run_max_cost_usd": settings.orchestrator_run_max_cost_usd,
                }
            )
        final_state = graph.invoke(initial_state, config=config)

    state_snapshot = graph.get_state(config)
    reporter.phase("finalising", "Preparing the structured result.")
    result = _map_state_to_output(
        request_id,
        final_state,
        execute_coding_agent,
        state_snapshot=state_snapshot,
        thread_id=thread_id,
    )
    try:
        record_run_audit_summary(
            request_id=request_id,
            thread_id=thread_id,
            state=final_state,
            result=result,
            settings=settings or load_settings(),
        )
    except Exception:
        # Audit is additive observability. Never change the workflow result or
        # resume contract because the local receipt store is unavailable.
        logger.exception("[AUDIT] Could not persist run summary for request_id=%s", request_id)
    final_target_project_context = TargetProjectContext.from_payload(
        final_state.get("target_project_context")
    )
    return result, final_target_project_context


def _map_decision_to_resume_payload(kind: str, decision: _Decision) -> Any:
    """Translate a generic decision into the resume value this graph's interrupt expects.

    This is the one place AI Tech Lead's own graph-internal resume shapes are allowed
    to leak in — callers only ever see the generic option/text contract.
    """

    options = {opt["name"]: opt for opt in _DECISION_OPTIONS_BY_KIND[kind]}
    option_spec = options.get(decision.option)
    if option_spec is None:
        valid = ", ".join(sorted(options))
        raise _DecisionRejected(
            f"'{decision.option}' is not a valid option for a pending {kind} decision. "
            f"Valid options: {valid}."
        )
    if option_spec.get("needs_text") and not decision.text:
        raise _DecisionRejected(f"decision.text is required for option '{decision.option}'.")

    if kind == "approval":
        payload: dict[str, Any] = {"action": decision.option}
        if decision.option == "approve":
            payload["approved_by"] = decision.actor or "agent-caller"
        elif decision.option == "request_changes":
            payload["feedback"] = decision.text
        elif decision.option == "ask_question":
            payload["question"] = decision.text
        return payload

    if kind in {"plan_guidance", "failure_guidance", "context_clarification"}:
        return decision.text

    if kind == "validation_command":
        # The interrupt node reads a plain string: the command, or "skip".
        return decision.text if decision.option == "answer" else "skip"

    if kind == "research_approval":
        return {"approved": decision.option == "approve"}

    if kind == "project_guidance_governance":
        return {"approved": decision.option == "approve"}

    if kind == "completion_verification":
        if decision.option == "confirm_complete":
            return {"decision": "confirm_complete"}
        return {"decision": "reject", "text": decision.text}

    if kind == "integration_approval":
        payload = {"action": decision.option}
        if decision.option == "approve":
            payload["approved_by"] = decision.actor or "agent-caller"
        return payload

    raise _DecisionRejected(f"Unknown pending interrupt kind: {kind}")


def _prompt_for_pending_interrupt(kind: str, payload: dict[str, Any]) -> str:
    if kind == "approval":
        reason = str(payload.get("reason", "")).strip()
        formulated_task = str(payload.get("formulated_task", "")).strip()
        last_question = str(payload.get("last_question", "")).strip()
        last_answer = str(payload.get("last_answer", "")).strip()
        parts = [f"Approval required: {reason}" if reason else "Approval required."]
        if formulated_task:
            parts.append(f"Proposed task: {formulated_task}")
        if last_question:
            parts.append(f"Q: {last_question}\nA: {last_answer}")
        return "\n".join(parts)

    if kind == "plan_guidance":
        reason = str(payload.get("reason", "")).strip()
        rejection_count = payload.get("rejection_count", 0)
        return f"Plan needs guidance (rejected {rejection_count}x): {reason}"

    if kind == "failure_guidance":
        retry_count = payload.get("retry_count", 0)
        result_excerpt = str(payload.get("coding_agent_result", "")).strip()[:200]
        return f"Coding agent failed {retry_count}x and needs guidance: {result_excerpt}"

    if kind == "research_approval":
        return str(payload.get("question", "")).strip() or "Online research approval needed."

    if kind == "context_clarification":
        question = str(payload.get("question", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        parts = [f"Context clarification needed: {question}" if question else "Context clarification needed."]
        if reason:
            parts.append(f"Reason: {reason}")
        return "\n".join(parts)

    if kind == "validation_command":
        question = str(payload.get("question", "")).strip()
        return question or (
            "Provide the project's validation command, or answer 'skip' to fall "
            "back to human verification at completion."
        )

    if kind == "completion_verification":
        reason = str(payload.get("reason", "")).strip()
        return f"Completion cannot be verified automatically and needs human review: {reason}"

    if kind == "integration_approval":
        prompt = str(payload.get("prompt", "")).strip()
        return prompt or "Validated work is on a pushed task branch and needs approval to enter main."

    if kind == "project_guidance_governance":
        status = str(payload.get("status", "")).strip() or "issue"
        summary = str(payload.get("summary", "")).strip()
        proposed_change = str(payload.get("proposed_change", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        parts = [f"Project guidance {status}: {summary}" if summary else f"Project guidance {status}."]
        if proposed_change:
            parts.append(f"Proposed change: {proposed_change}")
        if reason:
            parts.append(f"Reason: {reason}")
        return "\n".join(parts)

    return ""


def _pending_decision_from_snapshot(
    state_snapshot: Any | None,
    thread_id: str,
) -> dict[str, Any] | None:
    """Return the generic decision descriptor for a paused, resumable interrupt."""

    interrupt_value = _pending_interrupt_value(state_snapshot)
    if interrupt_value is None:
        return None
    kind = str(interrupt_value.get("kind", "")).strip()
    options = _DECISION_OPTIONS_BY_KIND.get(kind)
    if options is None:
        # Paused on something this runner doesn't know how to describe generically —
        # surface that it's paused without inventing a contract that doesn't exist.
        return {"thread_id": thread_id, "kind": kind or "unknown", "prompt": "", "options": []}
    return {
        "thread_id": thread_id,
        "kind": kind,
        "prompt": _prompt_for_pending_interrupt(kind, interrupt_value),
        "options": options,
    }


def _sync_backlog_completion(
    *,
    request_id: str,
    target_project_context: TargetProjectContext,
    result: dict[str, Any],
    settings: Any,
) -> str:
    """Write the backlog item's Status/Evidence back only on real execution success.

    `target_project_context` must be the *final* context — the one read back
    after the graph finished — since `backlog_item` may have been resolved
    mid-run (known-backlog-resolution path) rather than supplied up front.
    Both paths populate `backlog_item.row_hash` at fetch time, so this is the
    single sync mechanism regardless of which one resolved the item.

    Returns the sync status: "not_applicable" (no execution happened this
    run), "synced", "pending" (Sheets call failed, queued for recovery),
    "conflict" (source row changed since the snapshot), or "abandoned".
    """
    if not (
        result.get("status") == STATUS_SUCCESS
        and result.get("result_kind") == RESULT_KIND_EXECUTION_RESULT
    ):
        return "not_applicable"

    if str(result.get("main_status", "")).strip().startswith("MAIN STATUS: NOT IN MAIN"):
        logger.info(
            "Leaving backlog item open because validated task branch is not in main: %s",
            result.get("main_status", ""),
        )
        return "not_applicable"

    backlog_item = target_project_context.backlog_item
    if backlog_item is None:
        return "not_applicable"
    assert backlog_item.row_hash, (
        f"backlog_item '{backlog_item.item_id}' was resolved without a captured "
        "row_hash; both fetch paths must populate it."
    )

    backlog_project = target_project_context.backlog_project
    columns = backlog_project.columns if backlog_project is not None else BacklogColumnContext()
    sheets_repository = repository_for(
        backlog_item.spreadsheet_id,
        backlog_item.sheet_name,
        credentials_path=settings.backlog_google_credentials_path,
        layout=BacklogSheetLayout(columns=columns),
    )

    validation_note = f"Completed via Agent Hub request {request_id}. {result.get('summary', '')}"
    runtime_store = BacklogRuntimeStore()
    sync_status = enqueue_and_flush_update(
        runtime_store=runtime_store,
        sheets_repository=sheets_repository,
        request_id=request_id,
        item_id=backlog_item.item_id,
        update_fields={
            columns.status: "Done",
            columns.evidence_validation: validation_note.strip(),
        },
        expected_row_hash=backlog_item.row_hash,
        max_attempts=settings.backlog_pending_update_max_attempts,
    )
    runtime_store.mark_snapshot_status(
        request_id, "completed" if sync_status == "synced" else sync_status
    )
    if sync_status != "synced":
        logger.warning(
            "[SUBPROCESS] backlog sync for request_id=%s item_id=%s ended in status=%s",
            request_id,
            backlog_item.item_id,
            sync_status,
        )
    return sync_status


def _authoritative_validation_field(state: dict[str, Any]) -> str | None:
    """The validation line for the subprocess contract (ATL-039).

    Prefer the AI Tech Lead's own run — it re-runs the project's canonical
    command itself, so its result is authoritative. Fall back to the coding
    agent's self-declared line only when ATL had no command or could not run
    one.
    """

    passed = state.get("validation_passed")
    command = str(state.get("validation_command", "")).strip()
    if passed is not None and command:
        outcome = "passed" if passed else "failed"
        return f"{command} — {outcome} (AI Tech Lead ran it)"
    return str(state.get("coding_agent_validation", "")).strip() or None


def _map_state_to_output(
    request_id: str,
    state: dict[str, Any],
    execute_coding_agent: bool,
    *,
    state_snapshot: Any | None = None,
    thread_id: str = "",
) -> dict[str, Any]:
    agent_instruction = state.get("agent_instruction", "")
    formulated_task = state.get("formulated_task", "")
    brief = state.get("brief", "")
    orchestrator_input_required = state.get("orchestrator_input_required", False)
    orchestrator_input_question = state.get("orchestrator_input_question", "")
    coding_agent_success = state.get("coding_agent_success")
    coding_agent_result = state.get("coding_agent_result", "")
    restart_required = state.get("restart_required", False)
    main_status = str(state.get("git_main_status", "")).strip()
    lifecycle_enabled = bool(state.get("git_lifecycle_enabled"))

    pending_interrupt = _pending_interrupt_value(state_snapshot)
    pending_interrupt_kind = (
        str(pending_interrupt.get("kind", "")).strip()
        if isinstance(pending_interrupt, dict)
        else ""
    )

    pending_decision: dict[str, Any] | None = None

    if state.get("run_budget_terminated", False):
        status = STATUS_FAILED
        summary = (
            str(state.get("run_budget_exceeded_reason", "")).strip()
            or "Orchestrator run budget reached."
        )
        next_action = (
            "Review orchestrator run-budget settings and submit a new run; "
            "the subprocess will not raise its own budget automatically."
        )
        result_kind = RESULT_KIND_TERMINAL_FAILURE
    elif pending_interrupt_kind in _DECISION_OPTIONS_BY_KIND:
        pending_decision = _pending_decision_from_snapshot(state_snapshot, thread_id)
        status = STATUS_WAITING_DECISION
        summary = pending_decision["prompt"] if pending_decision else "A decision is required."
        option_names = ", ".join(opt["name"] for opt in pending_decision["options"])
        next_action = f"Resubmit request_id with a decision. Options: {option_names}."
        result_kind = RESULT_KIND_DECISION_REQUIRED
    elif state.get("backlog_item_not_found_reason"):
        # The project's backlog location was already known, so this is a definite
        # answer (the row isn't there), not something a human needs to interpret —
        # stop clearly instead of asking a clarification question we don't need.
        status = STATUS_FAILED
        summary = str(state.get("backlog_item_not_found_reason", ""))
        next_action = "Verify the item ID and resubmit — do not resubmit unchanged."
        result_kind = RESULT_KIND_TERMINAL_FAILURE
    elif state.get("context_clarification_exhausted"):
        # Context clarification was asked once and resumed, but the reference is
        # still unresolved. This must not invite another automated retry loop
        # (a caller like Agent Hub could otherwise resubmit forever) — it needs a
        # person to look at it directly.
        status = STATUS_FAILED
        summary = f"Context could not be resolved after 1 clarification attempt: {orchestrator_input_question}"
        next_action = "Needs direct human review — do not resubmit automatically."
        result_kind = RESULT_KIND_TERMINAL_FAILURE
    elif orchestrator_input_required:
        # No real interrupt behind this — the workflow ended rather than paused
        # (e.g. an unresolved reference). There is nothing to resume; the only
        # path forward is a brand new task that answers the question.
        status = STATUS_NEEDS_CLARIFICATION
        summary = f"Clarification needed: {orchestrator_input_question}"
        next_action = f"Answer the question and submit a new task: {orchestrator_input_question}"
        result_kind = RESULT_KIND_CLARIFICATION_REQUEST
    elif state.get("project_guidance_rejected_summary"):
        # Guidance was flagged as missing/conflicting and required review; the
        # human rejected the proposal, so this ends clearly rather than
        # proceeding on an admitted gap or an unresolved conflict.
        status = STATUS_FAILED
        summary = str(state.get("project_guidance_rejected_summary", ""))
        next_action = "Review the proposed project guidance change and resubmit if appropriate."
        result_kind = RESULT_KIND_TERMINAL_FAILURE
    elif agent_instruction:
        verification_status = state.get("verification_status", "")
        if coding_agent_success is True and verification_status == "failed":
            status = STATUS_FAILED
            verification_reason = state.get("verification_reason", "")
            summary = f"AI Tech Lead completion verification failed: {verification_reason[:200]}"
            next_action = "Review the unresolved verification issue."
            result_kind = RESULT_KIND_TERMINAL_FAILURE
        elif coding_agent_success is True:
            if lifecycle_enabled and not main_status.startswith("MAIN STATUS: IN MAIN"):
                status = (
                    STATUS_FAILED
                    if state.get("git_integration_status") == "blocked"
                    else STATUS_SUCCESS
                )
                branch = str(state.get("git_task_branch", "")).strip() or "the task branch"
                summary = f"Task validated on {branch}; it is not in main."
                next_action = (
                    "Resolve the integration issue, then retry integration."
                    if state.get("git_integration_status") == "blocked"
                    else "Approve integration to current origin/main."
                )
            else:
                status = STATUS_SUCCESS
                summary = "Task validated and integrated into main."
                next_action = (
                    "Restart the AI Tech Lead runtime, then review output."
                    if restart_required
                    else "Review output."
                )
            result_kind = RESULT_KIND_EXECUTION_RESULT
        elif coding_agent_success is False:
            status = STATUS_FAILED
            summary = f"Coding agent failed: {coding_agent_result[:200]}"
            next_action = "Submit instruction to coding backend."
            result_kind = RESULT_KIND_TERMINAL_FAILURE
        else:
            status = STATUS_SUCCESS
            summary = "Instruction generated. Ready for coding agent execution."
            next_action = "Submit instruction to coding backend."
            result_kind = RESULT_KIND_INSTRUCTION_PACKAGE
    elif restart_required:
        # Restart is an operational follow-up, not a task result. Preserve the
        # explicit terminal outcome above when a coding run already reported it.
        status = STATUS_FAILED
        summary = "Agent workflow requires a restart."
        next_action = "Retry the task from scratch."
        result_kind = RESULT_KIND_TERMINAL_FAILURE
    else:
        status = STATUS_FAILED
        summary = "Workflow completed without producing an instruction."
        next_action = "Check logs and retry with more specific task description."
        result_kind = RESULT_KIND_TERMINAL_FAILURE

    return {
        "request_id": request_id,
        "status": status,
        "summary": summary,
        "formulated_task": formulated_task,
        "brief": brief,
        "coding_agent_instruction": agent_instruction,
        "backend_used": state.get("coding_agent_performed_by", "none") or "none",
        "execution_performed": bool(state.get("coding_agent_performed_by")),
        "validation": _authoritative_validation_field(state),
        "logs": state.get("task_feedback", []),
        "evidence": list(state.get("research_source_titles", [])),
        "next_action": next_action,
        "result_kind": result_kind,
        "backlog_refinement": None,
        "pending_decision": pending_decision,
        "orchestrator_usage": {
            "calls": int(state.get("orchestrator_calls_used", 0)),
            "tokens_in": int(state.get("orchestrator_tokens_in_used", 0)),
            "tokens_out": int(state.get("orchestrator_tokens_out_used", 0)),
            "tokens_total": int(state.get("orchestrator_tokens_used", 0)),
            "cost_usd": float(state.get("orchestrator_cost_usd_used", 0.0)),
        },
        "main_status": main_status or None,
        "git": {
            "task_branch": str(state.get("git_task_branch", "")).strip() or None,
            "task_base_sha": str(state.get("git_task_base_sha", "")).strip() or None,
            "task_commit_sha": str(state.get("git_task_commit_sha", "")).strip() or None,
            "task_worktree": str(state.get("git_task_worktree", "")).strip() or None,
            "branch_pushed": bool(state.get("git_task_branch_pushed", False)),
            "main_status": main_status or None,
            "main_sha": str(state.get("git_main_sha", "")).strip() or None,
            "integration_status": str(state.get("git_integration_status", "")).strip() or None,
        },
        "backlog_sync_status": "not_applicable",
    }


def _pending_interrupt_value(state_snapshot: Any | None) -> dict[str, Any] | None:
    """Return the first interrupt payload from a paused graph snapshot, if present."""

    if state_snapshot is None or not getattr(state_snapshot, "next", ()):
        return None

    tasks = getattr(state_snapshot, "tasks", ())
    if not tasks:
        return None

    interrupts = getattr(tasks[0], "interrupts", ())
    if not interrupts:
        return None

    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else None


def _error_response(request_id: str, message: str, detail: str = "") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "status": STATUS_FAILED,
        "summary": message,
        "formulated_task": "",
        "brief": "",
        "coding_agent_instruction": "",
        "backend_used": "none",
        "execution_performed": False,
        "validation": None,
        "logs": [detail] if detail else [],
        "evidence": [],
        "next_action": "Fix the error and retry.",
        "result_kind": RESULT_KIND_TERMINAL_FAILURE,
        "backlog_refinement": None,
        "pending_decision": None,
        "backlog_sync_status": "not_applicable",
    }


def _backlog_refinement_output(
    *,
    request_id: str,
    status: str,
    summary: str,
    next_action: str,
    result_kind: str,
    backlog_refinement: BacklogRefinementProposal,
    pending_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "status": status,
        "summary": summary,
        "formulated_task": "",
        "brief": render_backlog_refinement_draft_text(backlog_refinement),
        "coding_agent_instruction": "",
        "backend_used": "none",
        "execution_performed": False,
        "validation": None,
        "logs": [],
        "evidence": [],
        "next_action": next_action,
        "result_kind": result_kind,
        "backlog_refinement": backlog_refinement.to_payload(),
        "pending_decision": pending_decision,
        "backlog_sync_status": "not_applicable",
    }


def _write_error_output(
    output_file: Path,
    reporter: ProgressReporter,
    request_id: str,
    message: str,
    *,
    detail: str = "",
    settings: Any | None,
) -> None:
    result = _error_response(request_id, message, detail)
    reporter.failed("AI Tech Lead could not complete the task.")
    _write_output(output_file, result, settings=settings)


def _write_output(
    output_file: Path,
    data: dict[str, Any],
    *,
    settings: Any | None = None,
) -> None:
    if "agent_manifest" not in data:
        data = dict(data)
        data["agent_manifest"] = agent_manifest_reference(settings)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[SUBPROCESS] Output written to %s (status=%s)", output_file, data.get("status"))
