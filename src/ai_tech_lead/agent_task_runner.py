"""Army-facing entry point: receive a task via JSON, return structured JSON output.

Called by the Agent Army orchestrator as a subprocess. No Telegram. No admin UI.

Security contract
-----------------
* project_root must be in settings.army_allowed_project_roots (server-side allowlist).
  Callers cannot inject arbitrary filesystem paths.
* Execution (running Codex / Claude Code) is gated on settings.execute_coding_agent.
  The caller's execution_mode is a *request*, not a grant.
  Unknown execution_mode values fail closed.
* requires_human_approval from the caller is ignored for the execution decision —
  that is the risk reviewer graph node's job.
* human_approved=true requires a valid approval_token previously issued by this process.
  Tokens are one-time use, expire after 1 hour, and are bound to the request/task pair.
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from pathlib import Path
from typing import Any

from .app_settings import load_settings
from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

STATUS_SUCCESS = "success"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_APPROVAL_REQUIRED = "approval_required"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"
ALLOWED_EXECUTION_MODES = {"instruction_only", "execute"}


def run_agent_task(input_path: str | Path, output_path: str | Path) -> int:
    """Read a task from input_path, run the workflow, write result to output_path.

    Returns 0 on success or soft outcomes (needs_clarification, approval_required).
    Returns 1 on hard failures.
    """
    input_file = Path(input_path)
    output_file = Path(output_path)

    try:
        task_input = json.loads(input_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _write_output(output_file, _error_response("", f"Could not read input file: {exc}"))
        return 1

    request_id = task_input.get("request_id") or str(uuid.uuid4())
    task_text = task_input.get("task", "").strip()

    if not task_text:
        _write_output(
            output_file,
            _error_response(request_id, "task field is required and must not be empty."),
        )
        return 1

    # Load local settings — security gates are server-side, not caller-controlled.
    try:
        settings = load_settings()
    except Exception as exc:
        _write_output(output_file, _error_response(request_id, f"Failed to load settings: {exc}"))
        return 1

    # Validate project_root against the local allowlist.
    project_root_raw = task_input.get("project_root")
    project_root = _validate_project_root(project_root_raw, settings)
    if project_root is None and project_root_raw is not None:
        msg = (
            f"project_root '{project_root_raw}' is not in the army_allowed_project_roots allowlist. "
            "Add it to data/coding_agent_settings.json to permit this path."
        )
        _write_output(output_file, _error_response(request_id, msg))
        return 1

    # Execution gate: local settings decide, not the caller.
    # The caller can *request* execution_mode=execute, but settings.execute_coding_agent
    # must also be True. The caller's requires_human_approval is intentionally ignored.
    execution_mode = _validate_execution_mode(task_input.get("execution_mode"))
    if execution_mode is None:
        _write_output(
            output_file,
            _error_response(
                request_id,
                "execution_mode must be one of: instruction_only, execute.",
            ),
        )
        return 1
    execute_coding_agent = execution_mode == "execute" and settings.execute_coding_agent

    # Approval token: human_approved=true requires a valid token issued by this process.
    human_approved = False
    if task_input.get("human_approved"):
        from .approval_store import consume_approval_token

        token = task_input.get("approval_token", "")
        if not consume_approval_token(token, request_id, task_text):
            msg = "human_approved=true requires a valid, unconsumed approval_token."
            _write_output(output_file, _error_response(request_id, msg))
            return 1
        human_approved = True

    logger.info("[ARMY] run-agent-task request_id=%s task=%s...", request_id, task_text[:80])

    try:
        result = _execute_workflow(
            request_id=request_id,
            task=task_text,
            execute_coding_agent=execute_coding_agent,
            project_root=project_root,
            human_approved=human_approved,
        )
    except Exception as exc:
        logger.exception("[ARMY] Unexpected error running workflow")
        _write_output(
            output_file,
            _error_response(request_id, f"Unexpected error: {exc}", traceback.format_exc()),
        )
        return 1

    # Issue an approval token when the workflow asks for human approval.
    if result.get("status") == STATUS_APPROVAL_REQUIRED:
        from .approval_store import create_approval_token

        token = create_approval_token(request_id, task_text)
        result["approval_token"] = token
        logger.info("[ARMY] approval_required — issued token for request_id=%s", request_id)

    _write_output(output_file, result)
    return (
        0
        if result.get("status")
        in (STATUS_SUCCESS, STATUS_NEEDS_CLARIFICATION, STATUS_APPROVAL_REQUIRED)
        else 1
    )


def _validate_project_root(project_root_raw: str | None, settings: Any) -> str | None:
    """Return the resolved project_root if valid, or None if it was provided but not allowed.

    If project_root_raw is None, returns None (caller did not specify one; use default).
    """
    if project_root_raw is None:
        return None
    requested = str(Path(project_root_raw).resolve())
    allowed = {str(Path(r).resolve()) for r in settings.army_allowed_project_roots}
    if requested in allowed:
        return requested
    return None  # caller provided a value but it failed the allowlist check


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


def _execute_workflow(
    *,
    request_id: str,
    task: str,
    execute_coding_agent: bool,
    project_root: str | None,
    human_approved: bool = False,
) -> dict[str, Any]:
    from langgraph.checkpoint.memory import MemorySaver

    from .coding_workflow_graph import build_graph

    graph = build_graph(
        checkpointer_storage=MemorySaver(),
        execute_coding_agent_override=execute_coding_agent,
        project_root_override=project_root,
    )

    thread_id = f"army-{request_id}"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: dict[str, Any] = {
        "request": task,
        # Army resubmissions are already approved; backlog-driven approval forcing stays local
        # to the coding workflow graph and should not be reintroduced here.
        "force_approval": False,
        "approved": human_approved,
        "approved_by": "human-via-army" if human_approved else "",
        "online_research_approved": True,
        "orchestrator_input_required": False,
        "orchestrator_input_kind": "",
        "orchestrator_input_reason": "",
        "orchestrator_input_question": "",
        "orchestrator_input_source_node": "",
        "needs_approval": False,
        "approval_reason": "",
        "task_feedback": [],
    }

    final_state = graph.invoke(initial_state, config=config)
    return _map_state_to_output(request_id, final_state, execute_coding_agent)


def _map_state_to_output(
    request_id: str, state: dict[str, Any], execute_coding_agent: bool
) -> dict[str, Any]:
    agent_instruction = state.get("agent_instruction", "")
    formulated_task = state.get("formulated_task", "")
    brief = state.get("brief", "")
    needs_approval = state.get("needs_approval", False)
    approved = state.get("approved", False)
    orchestrator_input_required = state.get("orchestrator_input_required", False)
    orchestrator_input_question = state.get("orchestrator_input_question", "")
    coding_agent_success = state.get("coding_agent_success")
    coding_agent_result = state.get("coding_agent_result", "")
    restart_required = state.get("restart_required", False)

    if orchestrator_input_required:
        status = STATUS_NEEDS_CLARIFICATION
        summary = f"Clarification needed: {orchestrator_input_question}"
        next_action = f"Answer the question and resubmit: {orchestrator_input_question}"
    elif needs_approval and not approved:
        status = STATUS_APPROVAL_REQUIRED
        summary = f"Approval required: {state.get('approval_reason', '')}"
        next_action = (
            "Approve via Telegram, then resubmit with human_approved=true and the approval_token."
        )
    elif restart_required:
        status = STATUS_BLOCKED
        summary = "Agent workflow requires a restart."
        next_action = "Retry the task from scratch."
    elif agent_instruction:
        if coding_agent_success is True:
            status = STATUS_SUCCESS
            summary = "Task completed successfully by the coding agent."
        elif coding_agent_success is False:
            status = STATUS_BLOCKED
            summary = f"Coding agent failed: {coding_agent_result[:200]}"
        else:
            status = STATUS_SUCCESS
            summary = "Instruction generated. Ready for coding agent execution."
        next_action = (
            "Review output." if coding_agent_success else "Submit instruction to coding backend."
        )
    else:
        status = STATUS_BLOCKED
        summary = "Workflow completed without producing an instruction."
        next_action = "Check logs and retry with more specific task description."

    return {
        "request_id": request_id,
        "status": status,
        "summary": summary,
        "formulated_task": formulated_task,
        "brief": brief,
        "coding_agent_instruction": agent_instruction,
        "backend_used": state.get("coding_agent_performed_by", "none") or "none",
        "execution_performed": bool(coding_agent_success is not None),
        "logs": state.get("task_feedback", []),
        "evidence": list(state.get("research_source_titles", [])),
        "next_action": next_action,
    }


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
        "logs": [detail] if detail else [],
        "evidence": [],
        "next_action": "Fix the error and retry.",
    }


def _write_output(output_file: Path, data: dict[str, Any]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[ARMY] Output written to %s (status=%s)", output_file, data.get("status"))
