"""Machine-readable capability handshake for AI Tech Lead."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__
from .app_settings import AppSettings

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_COMMAND = "uv run python -m ai_tech_lead manifest"

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
    "blocked",
    "failed",
]


def build_agent_manifest(settings: AppSettings | None = None) -> dict[str, Any]:
    """Return the small machine-readable handshake other agents can cache."""

    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "agent_id": "ai-tech-lead",
        "agent_name": "AI Tech Lead",
        "package": "ai_tech_lead",
        "package_version": __version__,
        "role": "specialist coding lead inside the agent army",
        "one_line": (
            "Reads a bounded task, reasons as a tech lead, and returns a structured handoff "
            "or execution result."
        ),
        "entrypoints": {
            "json_subprocess": "run-agent-task",
            "manifest": "manifest",
            "setup": "setup",
            "doctor": "doctor",
            "knowledge_store": "knowledge-store stats|backup|restore|compact",
        },
        "input_contract": {
            "required": ["task"],
            "optional": [
                "request_id",
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
                "agent_manifest",
            ],
        },
        "capabilities": [
            "clarify ambiguous work",
            "review task risk",
            "request and review plans",
            "build a bounded coding-agent instruction",
            "run the configured coding backend when enabled",
            "maintain a local knowledge store",
            "report workspace health and knowledge-store stats",
        ],
        "boundaries": [
            "does not choose work without explicit input",
            "does not bypass approval gates",
            "does not edit files except through the configured coding-agent runner",
            "does not rely on Telegram as the agent-to-agent contract",
        ],
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
            "army_allowed_project_roots": list(settings.army_allowed_project_roots),
            "knowledge_store_path": settings.knowledge_store_path,
        }
    else:
        manifest["runtime"] = None

    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def render_agent_manifest(settings: AppSettings | None = None, *, compact: bool = False) -> str:
    """Render the handshake as JSON for other agents to cache."""

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
