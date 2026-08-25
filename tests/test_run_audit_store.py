from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from ai_tech_lead.run_audit_store import (
    RunAuditStore,
    record_run_audit_summary,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        orchestrator_ai_model="gpt-test",
        orchestrator_ai_profile="interview-safe",
    )


def test_schema_and_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = RunAuditStore(db_path)
    summary = record_run_audit_summary(
        request_id="ATL-038-test",
        thread_id="thread-1",
        state={
            "request": "Implement the bounded audit receipt.",
            "needs_approval": True,
            "approved": True,
            "approved_by": "operator",
            "approval_action": "approve",
            "approval_revision_count": 1,
            "git_preflight_branch": "feat/test",
            "git_preflight_head": "abc123",
            "git_preflight_status": "clean",
            "coding_agent_performed_by": "codex",
            "coding_agent_changed_files": ("src/example.py",),
            "verification_status": "complete",
        },
        result={
            "status": "success",
            "result_kind": "execution_result",
            "summary": "Completed.",
        },
        settings=_settings(),
        store=store,
    )

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(run_audit_summaries)")
        }
    assert {"request_id", "selected_skill_hashes", "validation_status"} <= columns

    restored = store.get("ATL-038-test")
    assert restored == summary
    assert restored.to_payload()["result"]["status"] == "success"


def test_secret_exclusion_and_bounded_summary(tmp_path: Path) -> None:
    store = RunAuditStore(tmp_path / "audit.sqlite3")
    summary = record_run_audit_summary(
        request_id="secret-test",
        thread_id="thread-2",
        state={
            "request": "Use OPENAI_API_KEY=sk-abcdefghijkl and password=do-not-store.",
            "coding_agent_command": "Bearer provider-secret",
        },
        result={
            "status": "failed",
            "summary": "Bearer another-secret",
        },
        settings=_settings(),
        store=store,
    )

    payload_text = json.dumps(summary.to_payload())
    assert "sk-abcdefghijkl" not in payload_text
    assert "do-not-store" not in payload_text
    assert "provider-secret" not in payload_text
    assert "another-secret" not in payload_text
    assert "prompt" not in summary.to_payload()
    assert "logs" not in summary.to_payload()


def test_compact_skill_versions_and_guidance_state_are_retrievable(tmp_path: Path) -> None:
    store = RunAuditStore(tmp_path / "audit.sqlite3")
    record_run_audit_summary(
        request_id="versions-test",
        thread_id="thread-3",
        state={
            "request": "Record selected instruction versions.",
            "selected_skill_paths": [".skills/code-change/SKILL.md"],
            "selected_skill_hashes": {".skills/code-change/SKILL.md": "sha256:123"},
            "project_guidance_paths": ["AGENTS.md"],
            "project_guidance_hash": "sha256:456",
            "project_guidance_changed_on_resume": True,
            "rubric_status": "passed",
        },
        result={
            "status": "waiting_decision",
            "result_kind": "paused",
            "tokens_in": 120,
            "tokens_out": 30,
            "cost_usd": 0.004,
        },
        settings=_settings(),
        store=store,
    )

    payload = store.get("versions-test").to_payload()
    assert payload["selected_skill_paths"] == [".skills/code-change/SKILL.md"]
    assert payload["selected_skill_hashes"] == {
        ".skills/code-change/SKILL.md": "sha256:123"
    }
    assert payload["guidance_changed_on_resume"] is True
    assert payload["rubric_status"] == "passed"
    assert payload["usage"] == {"tokens_in": 120, "tokens_out": 30, "cost_usd": 0.004}
    assert "SKILL CONTENT" not in json.dumps(payload)


def test_audit_prefers_persisted_orchestrator_run_usage(tmp_path: Path) -> None:
    store = RunAuditStore(tmp_path / "audit.sqlite3")
    record_run_audit_summary(
        request_id="budget-usage-test",
        thread_id="thread-budget",
        state={
            "request": "Use persisted run usage.",
            "orchestrator_tokens_in_used": 321,
            "orchestrator_tokens_out_used": 79,
            "orchestrator_cost_usd_used": 0.0123,
        },
        result={
            "status": "failed",
            "result_kind": "terminal_failure",
            "tokens_in": 1,
            "tokens_out": 1,
            "cost_usd": 0.0001,
        },
        settings=_settings(),
        store=store,
    )

    assert store.get("budget-usage-test").to_payload()["usage"] == {
        "tokens_in": 321,
        "tokens_out": 79,
        "cost_usd": 0.0123,
    }
