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
    assert manifest["entrypoints"]["manifest"] == "manifest"
    assert manifest["entrypoints"]["json_subprocess"] == "run-agent-task"
    assert "agent_manifest" in manifest["output_contract"]["fields"]
    assert manifest["output_contract"]["status_values"] == [
        "success",
        "needs_clarification",
        "approval_required",
        "failed",
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
