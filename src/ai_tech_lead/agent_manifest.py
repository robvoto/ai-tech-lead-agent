"""Machine-readable capability handshake for AI Tech Lead."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__
from .app_settings import AppSettings

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
    "approval_required",
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
            "Select for: Implementing backlog items or modifying code, tests, configuration, "
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
        },
        "input_contract": {
            "required": ["task"],
            "optional": [
                "request_id",
                "run_id",
                "source",
                "project_root",
                "execution_mode",
                "human_approved",
                "approval_token",
            ],
            "execution_modes": ["instruction_only", "execute"],
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
                "caller_action",
                "resume_supported",
                "resume_fields",
                "interrupt_kind",
                "agent_manifest",
            ],
        },
        "status_contract": {
            "success": {
                "terminal": True,
                "caller_action": "consume_result",
                "notes": (
                    "Returned when execution completed or when an instruction package is ready. "
                    "Use result_kind to distinguish which."
                ),
            },
            "needs_clarification": {
                "terminal": False,
                "caller_action": "provide_clarification",
                "notes": (
                    "Returned for resumable human-text interruptions such as plan guidance, "
                    "clarifying questions, or repeated coding-agent failure guidance."
                ),
            },
            "approval_required": {
                "terminal": False,
                "caller_action": "provide_approval",
                "notes": (
                    "Returned when explicit human approval is required. Resume with the "
                    "issued approval_token and human_approved=true."
                ),
            },
            "failed": {
                "terminal": True,
                "caller_action": "inspect_failure",
                "notes": "Returned for terminal failures that should not be treated as resumable.",
            },
        },
        "interaction_model": {
            "primary_interface": "bounded local JSON subprocess",
            "discovery_pattern": "manifest-style handshake inspired by an A2A agent card",
            "task_pattern": "request/response with resumable human interrupts",
            "supports_streaming": True,
            "streaming_scope": (
                "Structured progress events stream on stdout when run_id is supplied; "
                "the final result remains in the output JSON file."
            ),
            "supports_push_notifications": False,
            "resume_via_resubmission": True,
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
                "Owns transport, user/session orchestration, and collecting human input "
                "or approvals before resubmission."
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
