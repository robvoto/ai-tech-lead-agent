from __future__ import annotations

from pathlib import Path

import pytest

from ai_tech_lead.prompt_loader import (
    PROMPT_REGISTRY_PATH,
    clear_prompt_registry_cache,
    load_prompt,
    load_prompt_registry,
)


def test_default_prompt_registry_exists() -> None:
    assert PROMPT_REGISTRY_PATH.is_file()


def test_prompt_registry_exposes_central_catalog() -> None:
    registry = load_prompt_registry()

    assert [prompt.key for prompt in registry] == [
        "backlog_ownership_rules",
        "clarification_check",
        "completion_summary",
        "completion_verification",
        "plan_request_instruction",
        "plan_review",
        "risk_review",
        "research_knowledge_gap",
        "operator_question_answer",
        "telegram_agent_system",
        "risk_review_reason",
        "tech_lead_analysis",
        "atl_relevance_check",
    ]


def test_prompt_loader_reads_prompt_from_registry_correctly(monkeypatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "prompts.json"
    registry_path.write_text(
        (
            '{\n'
            '  "prompts": [\n'
            '    {\n'
            '      "key": "demo_prompt",\n'
            '      "prompt_class": "instruction",\n'
            '      "purpose": "Demo prompt",\n'
            '      "used_by": ["demo.py"],\n'
            '      "template": "Hello prompt"\n'
            '    }\n'
            '  ]\n'
            '}\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ai_tech_lead.prompt_loader.PROMPT_REGISTRY_PATH", registry_path)
    clear_prompt_registry_cache()

    assert load_prompt("demo_prompt") == "Hello prompt"


def test_missing_prompt_key_raises_clear_error(monkeypatch, tmp_path: Path) -> None:
    registry_path = tmp_path / "prompts.json"
    registry_path.write_text(
        '{"prompts":[{"key":"present","prompt_class":"instruction","purpose":"Demo","template":"Hi"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr("ai_tech_lead.prompt_loader.PROMPT_REGISTRY_PATH", registry_path)
    clear_prompt_registry_cache()

    with pytest.raises(FileNotFoundError, match="was not found in the prompt registry"):
        load_prompt("missing_prompt")


def test_backlog_ownership_prompt_points_to_current_backlog_policy_doc() -> None:
    prompt_text = load_prompt("backlog_ownership_rules")

    assert "docs/INDEX.md" in prompt_text
    assert "docs/BACKLOG_MANAGEMENT.md" not in prompt_text
    assert "data/backlog/archive/BACKLOG.md" not in prompt_text
