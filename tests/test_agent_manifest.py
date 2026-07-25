from __future__ import annotations

import json

from helpers import valid_settings_dict

from ai_tech_lead.agent_manifest import (
    MANIFEST_COMMAND,
    build_agent_manifest,
    render_agent_manifest,
)
from ai_tech_lead.app_settings import parse_settings


def test_build_agent_manifest_contains_core_contract_fields() -> None:
    settings = parse_settings(valid_settings_dict())

    manifest = build_agent_manifest(settings)

    assert manifest["manifest_schema_version"] == 1
    assert manifest["agent_id"] == "ai-tech-lead"
    assert manifest["agent_name"] == "AI Tech Lead"
    assert manifest["purpose"] == (
        "Primary responsibility: Lead and execute changes to existing software.\n"
        "Select for: Implementing backlog items or modifying code, tests, configuration, "
        "architecture, or documentation in an existing repository, including Agent Factory, "
        "Agent Hub, AI Tech Lead, or another existing software project.\n"
        "Do not select for: Designing, staging, approving, rejecting, or promoting a new "
        "specialist agent package as the requested deliverable."
    )
    assert "one_line" not in manifest
    assert "capabilities" not in manifest
    assert "boundaries" not in manifest
    assert manifest["manifest_cache_ttl_seconds"] == 3600
    assert manifest["entrypoints"]["manifest"] == "manifest"
    assert manifest["entrypoints"]["json_subprocess"] == "run-agent-task"
    assert manifest["entrypoints"]["bootstrap_project_pack"] == "bootstrap-project-pack"
    assert "agent_manifest" in manifest["output_contract"]["fields"]
    assert "result_kind" in manifest["output_contract"]["fields"]
    assert "pending_decision" in manifest["output_contract"]["fields"]
    assert manifest["output_contract"]["status_values"] == [
        "success",
        "needs_clarification",
        "waiting_decision",
        "failed",
    ]
    assert "run_id" in manifest["input_contract"]["optional"]
    assert manifest["progress_contract"]["transport"] == "stdout_jsonl"
    assert manifest["progress_contract"]["log_transport"] == "stderr"
    assert manifest["progress_contract"]["schema_version"] == 1
    assert manifest["interaction_model"]["resume_via_decision"] is True
    assert manifest["interaction_model"]["resume_via_resubmission"] is False
    assert manifest["interaction_model"]["supports_streaming"] is True
    assert manifest["ownership"]["specialist_repo"]
    assert manifest["project_pack"]["required_files"] == [
        "AGENTS.md",
        "docs/INDEX.md",
        ".skills/INDEX.md",
    ]
    assert manifest["runtime"]["project_root"] == settings.project_root
    assert manifest["runtime"]["knowledge_store_path"] == settings.knowledge_store_path
    assert len(manifest["manifest_hash"]) == 64


def test_render_agent_manifest_compact_is_single_line_json() -> None:
    settings = parse_settings(valid_settings_dict())

    rendered = render_agent_manifest(settings, compact=True)
    manifest = json.loads(rendered)

    assert "\n" not in rendered
    assert rendered.startswith("{")
    assert manifest["manifest_hash"]
    assert manifest["entrypoints"]["manifest"] == "manifest"
    assert MANIFEST_COMMAND == "uv run python -m ai_tech_lead manifest"
