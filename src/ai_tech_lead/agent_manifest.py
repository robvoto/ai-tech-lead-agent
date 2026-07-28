"""Machine-readable capability handshake for AI Tech Lead."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__
from .app_settings import AppSettings
from .target_project_context import (
    PROJECT_CONTEXT_SCHEMA_VERSION,
    SUPPORTED_PROJECT_CONTEXT_SCHEMA_VERSIONS,
)

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_COMMAND = "uv run python -m ai_tech_lead manifest"
MANIFEST_CACHE_TTL_SECONDS = 3600

_REQUIRED_DOCS = [
    "docs/INDEX.md",
    "docs/ARCHITECTURE.md",
    "docs/GRAPH_WORKFLOW.md",
    "docs/RUNTIME_RUNBOOK.md",
    "docs/STANDARDS_INDEX.md",
]

_SUPPORTED_OUTPUT_STATUSES = [
    "success",
    "needs_clarification",
    "waiting_decision",
    "failed",
]

_PROJECT_PACK_REQUIRED_FILES = [
    "AGENTS.md",
    "docs/INDEX.md",
    ".skills/INDEX.md",
]


def build_agent_manifest(settings: AppSettings | None = None) -> dict[str, Any]:
    """Return the small machine-readable handshake external callers can cache."""

    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "agent_id": "ai-tech-lead",
        "agent_name": "AI Tech Lead",
        "package": "ai_tech_lead",
        "package_version": __version__,
        "role": "specialist coding lead callable through a bounded local subprocess contract",
        "purpose": (
            "Primary responsibility: Lead and execute changes to existing software.\n"
            "Select for: Refining backlog items, implementing backlog items, or modifying code, tests, configuration, "
            "architecture, or documentation in an existing repository, including Agent Factory, "
            "Agent Hub, AI Tech Lead, or another existing software project.\n"
            "Do not select for: Designing, staging, approving, rejecting, or promoting a new "
            "specialist agent package as the requested deliverable."
        ),
        "manifest_cache_ttl_seconds": MANIFEST_CACHE_TTL_SECONDS,
        "entrypoints": {
            "json_subprocess": "run-agent-task",
            "manifest": "manifest",
            "bootstrap_project_pack": "bootstrap-project-pack",
            "setup": "setup",
            "doctor": "doctor",
            "knowledge_store": "knowledge-store stats|backup|restore|compact",
            "backlog_sync_recovery": "backlog-sync-recover",
        },
        "input_contract": {
            "required_for_new_task": ["task"],
            "required_to_resume": ["request_id", "decision"],
            "optional": [
                "request_id",
                "run_id",
                "source",
                "project_root",
                "task_kind",
                "execution_mode",
                "decision",
                "backlog_reference",
                "project_reference",
                "resource_references",
                "references",
                "project_context",
            ],
            "decision_shape": {
                "option": "required — must match one of the option names the paused "
                "response's pending_decision.options just reported",
                "text": "required only if the chosen option's needs_text is true",
                "actor": "optional — free-text identity of who or what is deciding",
            },
            "task_kinds": ["coding_task", "backlog_refinement"],
            "execution_modes": ["instruction_only", "execute"],
            "context_resolution": (
                "AI Tech Lead understands and bounds the request, resolves explicit project/resource "
                "context, and asks one clarification question for unresolved references before research."
            ),
            "project_reference_backlog_shape": {
                "project_key": "optional descriptive alias for the target backlog",
                "spreadsheet_id": "required for backlog_refinement when no local alias resolves it",
                "sheet_name": "required for backlog_refinement when no local alias resolves it",
                "item_id_prefix": "optional; if omitted, AI Tech Lead infers it from the backlog when unambiguous",
                "columns": "optional object overriding project-specific column names for refined backlog fields",
            },
            "project_context_shape": {
                "schema_version": "optional, defaults to 1 — matches Agent Factory's AF-054 "
                "ProjectContext shape",
                "project_root": "optional; must agree with top-level project_root if both are sent",
                "references": "optional list of pointer strings; equivalent to top-level references",
                "note": "Legacy flat top-level project_root/references are still accepted "
                "(implicit schema_version 1) for callers that don't send this envelope yet.",
            },
            "backlog_reference_shape": {
                "item_id": "required",
                "project_key": "required unless spreadsheet_id and sheet_name are both given",
                "spreadsheet_id": "required unless project_key resolves via a configured alias",
                "sheet_name": "required unless project_key resolves via a configured alias",
            },
        },
        "project_context_contract": {
            "supported_schema_versions": sorted(SUPPORTED_PROJECT_CONTEXT_SCHEMA_VERSIONS),
            "required": False,
            "capabilities": ["read", "write"],
            "enforced_filesystem_permission": "write",
            "notes": (
                "Matches Agent Factory's AF-054 ProjectContextContract fields (not imported — "
                "inlined per that module's own scope note). AI Tech Lead does not require a "
                "target project for every task, so `required` is false; when one is supplied "
                "it may be read and, in execute mode, written to."
            ),
        },
        "output_contract": {
            "status_values": _SUPPORTED_OUTPUT_STATUSES,
            "fields": [
                "request_id",
                "status",
                "summary",
                "formulated_task",
                "brief",
                "coding_agent_instruction",
                "backend_used",
                "execution_performed",
                "logs",
                "evidence",
                "next_action",
                "result_kind",
                "backlog_refinement",
                "pending_decision",
                "backlog_sync_status",
                "agent_manifest",
            ],
            "backlog_refinement_shape": {
                "draft": "structured refined backlog draft proposed by AI Tech Lead",
                "source": "model/backend that produced the draft",
                "skill_path": "runtime skill file loaded for backlog-item authoring",
                "matches": "list of matching backlog IDs with kind/status/reason",
                "blocked": "true when AI Tech Lead found blocking duplicate, already-done, obsolete, or conflicting-scope matches",
            },
            "pending_decision_shape": {
                "thread_id": "opaque; informational only, not required on the resume call",
                "kind": "which internal pause this is (not meaningful to interpret, "
                "only to relay)",
                "prompt": "plain-language description of what's paused, to show a human "
                "or pass to another agent",
                "options": "list of {name, needs_text}; the exact and only valid "
                "decision.option values for this pause",
            },
            "backlog_sync_status_values": [
                "not_applicable",
                "synced",
                "pending",
                "conflict",
                "abandoned",
            ],
        },
        "status_contract": {
            "success": {
                "terminal": True,
                "notes": (
                    "Returned when execution completed or when an instruction package is ready. "
                    "Use result_kind to distinguish which."
                ),
            },
            "needs_clarification": {
                "terminal": True,
                "notes": (
                    "Returned when the request ended needing more information but there is no "
                    "paused conversation to resume (e.g. an unresolved reference). Submit a new "
                    "task with the answer folded in — there is nothing to send a decision against."
                ),
            },
            "waiting_decision": {
                "terminal": False,
                "notes": (
                    "Returned whenever the workflow is genuinely paused (approval, plan or "
                    "failure guidance, research approval, completion verification, or backlog refinement approval). See "
                    "pending_decision for exactly what options are valid right now. Resume by "
                    "resubmitting request_id with a decision — no task field needed."
                ),
            },
            "failed": {
                "terminal": True,
                "notes": "Returned for terminal failures that should not be treated as resumable.",
            },
        },
        "interaction_model": {
            "primary_interface": "bounded local JSON subprocess",
            "discovery_pattern": "manifest-style handshake inspired by an A2A agent card",
            "task_pattern": "request/response with a durable, resumable paused conversation",
            "supports_streaming": True,
            "streaming_scope": (
                "Structured progress events stream on stdout when run_id is supplied; "
                "the final result remains in the output JSON file."
            ),
            "supports_push_notifications": False,
            "resume_via_decision": True,
            "resume_via_resubmission": False,
            "tool_protocol_note": (
                "Use MCP for tools/resources behind this agent. Use this manifest and "
                "run-agent-task for the caller boundary."
            ),
        },
        "progress_contract": {
            "optional": True,
            "activation": "caller supplies run_id",
            "schema_version": 1,
            "transport": "stdout_jsonl",
            "log_transport": "stderr",
            "event_fields": [
                "schema_version",
                "run_id",
                "request_id",
                "sequence",
                "event_type",
                "phase",
                "human_summary",
                "occurred_at",
                "metadata",
            ],
            "notes": (
                "Progress is operational telemetry only. It never contains chain-of-thought, "
                "full prompts, raw provider payloads, or unbounded logs."
            ),
        },
        "ownership": {
            "specialist_repo": (
                "Owns specialist-internal workflow logic, prompts, status mapping, "
                "instruction assembly, and reusable runtime skills."
            ),
            "caller": (
                "Owns transport, user/session orchestration, and collecting the human or "
                "agent decision before sending it back as a resume."
            ),
        },
        "project_pack": {
            "bootstrap_command": "uv run python -m ai_tech_lead bootstrap-project-pack TARGET_ROOT",
            "required_files": _PROJECT_PACK_REQUIRED_FILES,
            "instruction_layers": [
                "runtime_core/CORE.md",
                "runtime_core/skills/*",
                "TARGET_ROOT/AGENTS.md",
                "TARGET_ROOT/.skills/INDEX.md and selected skill files",
            ],
        },
        "required_docs": _REQUIRED_DOCS,
        "knowledge_store": {
            "purpose": "bounded agent memory for docs, trusted sources, learnings, and preferences",
            "path": "data/knowledge_store.sqlite3",
        },
    }

    if settings is not None:
        manifest["runtime"] = {
            "project_root": settings.project_root,
            "execute_coding_agent": settings.execute_coding_agent,
            "telegram_enabled": settings.telegram_enabled,
            "orchestrator_ai_enabled": settings.orchestrator_ai_enabled,
            "allowed_directories": list(settings.allowed_directories),
            "subprocess_allowed_project_roots": list(settings.allowed_project_roots),
            "knowledge_store_path": settings.knowledge_store_path,
        }
    else:
        manifest["runtime"] = None

    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def render_agent_manifest(settings: AppSettings | None = None, *, compact: bool = False) -> str:
    """Render the handshake as JSON for external callers to cache."""

    manifest = build_agent_manifest(settings)
    if compact:
        return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)


def agent_manifest_reference(settings: AppSettings | None = None) -> dict[str, str]:
    """Return a tiny cache-friendly manifest reference for task responses."""

    manifest = build_agent_manifest(settings)
    return {
        "agent_id": manifest["agent_id"],
        "package_version": manifest["package_version"],
        "manifest_hash": manifest["manifest_hash"],
        "manifest_command": MANIFEST_COMMAND,
    }


def _manifest_hash(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_hash", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
