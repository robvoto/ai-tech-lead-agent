from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.research_cache import (
    ResearchCacheEntry,
    format_research_cache_for_prompt,
    load_research_cache_entries,
)
from ai_tech_lead.research_checker import (
    ResearchCheckResult,
    check_research_requirements,
)

from helpers import valid_settings_dict


def test_load_research_cache_entries_reads_index_and_note(tmp_path: Path) -> None:
    research_dir = tmp_path / "docs" / "research"
    research_dir.mkdir(parents=True)
    note_path = research_dir / "backlog-refinement.md"
    note_path.write_text(
        "---\n"
        "topic: Backlog refinement with cache-first research\n"
        "date: 2026-06-01\n"
        "sources:\n"
        "  - https://docs.langchain.com/oss/python/langchain/structured-output\n"
        "---\n\n"
        "## Summary\n"
        "Use a cache-first refinement flow.\n",
        encoding="utf-8",
    )
    index_path = research_dir / "INDEX.md"
    index_path.write_text(
        "# Research Cache Index\n\n"
        "## Backlog / Refinement\n\n"
        "- [backlog-refinement.md](backlog-refinement.md) — Cache-first backlog refinement (2026-06-10)\n",
        encoding="utf-8",
    )

    entries = load_research_cache_entries(index_path=index_path, today=date(2026, 6, 10))
    prompt_block = format_research_cache_for_prompt(entries, limit=800)

    assert len(entries) == 1
    assert entries[0].title == "Backlog refinement with cache-first research"
    assert "Freshness risk: Low" in prompt_block
    assert "Cache-first backlog refinement" in prompt_block


def _make_fake_entry(title: str) -> ResearchCacheEntry:
    return ResearchCacheEntry(
        path=Path("/fake/path.md"),
        title=title,
        summary="Fake summary.",
        body="",
        sources=[],
        refreshed_on=date(2026, 6, 1),
        freshness_risk="Low: recent.",
    )


def test_check_research_requirements_simple_task_skips_cache(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (False, "Simple bug fix."),
    )

    result = check_research_requirements("Fix typo in README", settings)

    assert result.is_complex is False
    assert result.online_research_needed is False
    assert result.sources_found == 0


def test_check_research_requirements_complex_with_two_sources_continues(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (True, "Involves LangGraph interrupt nodes."),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker._load_cache_entries",
        lambda: [_make_fake_entry("LangGraph approval"), _make_fake_entry("Backlog patterns")],
    )

    result = check_research_requirements("Add CLARIFICATION_GATE to workflow", settings)

    assert result.is_complex is True
    assert result.sources_found == 2
    assert result.online_research_needed is False
    assert len(result.usable_source_titles) == 2


def test_check_research_requirements_complex_with_one_source_triggers_gate(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (True, "Involves LangGraph interrupt nodes."),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker._load_cache_entries",
        lambda: [_make_fake_entry("LangGraph approval")],
    )

    result = check_research_requirements("Add CLARIFICATION_GATE to workflow", settings)

    assert result.is_complex is True
    assert result.sources_found == 1
    assert result.online_research_needed is True


def test_check_research_requirements_ai_disabled_treats_as_simple() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=False)

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.is_complex is False
    assert result.online_research_needed is False


def test_check_research_requirements_online_not_triggered_without_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (True, "Complex: LangGraph persistence."),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker._load_cache_entries",
        lambda: [],
    )

    result = check_research_requirements("Add checkpointing to workflow", settings)

    assert result.online_research_needed is True
    assert result.is_complex is True
    assert result.sources_found == 0
