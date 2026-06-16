from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.orchestrator_llm import OrchestratorLlmError
from ai_tech_lead.research_cache import (
    format_research_cache_for_prompt,
    load_research_cache_entries,
    save_online_source_to_cache,
)
from ai_tech_lead.research_checker import (
    check_research_requirements,
)
from ai_tech_lead.research_sources import ResearchSource


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
        "- [backlog-refinement.md](backlog-refinement.md) — "
        "Cache-first backlog refinement (2026-06-10)\n",
        encoding="utf-8",
    )

    entries = load_research_cache_entries(index_path=index_path, today=date(2026, 6, 10))
    prompt_block = format_research_cache_for_prompt(entries, limit=800)

    assert len(entries) == 1
    assert entries[0].title == "Backlog refinement with cache-first research"
    assert "Freshness risk: Low" in prompt_block
    assert "Cache-first backlog refinement" in prompt_block


def _make_fake_source(title: str, location: str = "/fake/path.md") -> ResearchSource:
    return ResearchSource(
        title=title,
        location=location,
        summary="Fake summary.",
        excerpt="",
        source_kind="local-doc",
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
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda _request, _settings: [
            _make_fake_source("LangGraph approval"),
            _make_fake_source("Backlog patterns"),
        ],
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
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda _request, _settings: [_make_fake_source("LangGraph approval")],
    )

    result = check_research_requirements("Add CLARIFICATION_GATE to workflow", settings)

    assert result.is_complex is True
    assert result.sources_found == 1
    assert result.online_research_needed is True


def test_check_research_requirements_ai_disabled_requires_approval() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=False)

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.is_complex is True
    assert result.online_research_needed is True
    assert "requiring human approval" in result.complexity_reason.lower()


def test_check_research_requirements_llm_unavailable_requires_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (_ for _ in ()).throw(OrchestratorLlmError("offline")),
    )

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.is_complex is True
    assert result.online_research_needed is True
    assert "unavailable" in result.complexity_reason.lower()


def test_check_research_requirements_invalid_llm_response_requires_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (_ for _ in ()).throw(ValueError("bad payload")),
    )

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.is_complex is True
    assert result.online_research_needed is True
    assert "invalid" in result.complexity_reason.lower()


def test_check_research_requirements_online_not_triggered_without_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_complexity",
        lambda _req, _settings: (True, "Complex: LangGraph persistence."),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda _request, _settings: [],
    )

    result = check_research_requirements("Add checkpointing to workflow", settings)

    assert result.online_research_needed is True
    assert result.is_complex is True
    assert result.sources_found == 0


def test_save_online_source_to_cache_writes_note_and_index(tmp_path: Path) -> None:
    research_dir = tmp_path / "docs" / "research"

    saved = save_online_source_to_cache(
        title="LangGraph interrupts",
        location="https://docs.langchain.com/oss/python/langgraph/interrupts",
        summary="Interrupts pause graph execution and resume with Command.",
        excerpt="",
        project_root=tmp_path,
        today=date(2026, 6, 15),
    )

    assert saved is True
    note_path = research_dir / "langgraph-interrupts.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "topic: LangGraph interrupts" in content
    assert "date: 2026-06-15" in content
    assert "https://docs.langchain.com/oss/python/langgraph/interrupts" in content
    assert "## Summary" in content
    assert "Interrupts pause graph execution" in content

    index_content = (research_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "langgraph-interrupts.md" in index_content
    assert "Interrupts pause graph execution" in index_content


def test_save_online_source_to_cache_skips_duplicate_url(tmp_path: Path) -> None:
    kwargs = dict(
        title="LangGraph interrupts",
        location="https://docs.langchain.com/oss/python/langgraph/interrupts",
        summary="Interrupts pause graph execution.",
        excerpt="",
        project_root=tmp_path,
        today=date(2026, 6, 15),
    )

    first = save_online_source_to_cache(**kwargs)
    second = save_online_source_to_cache(**kwargs)

    assert first is True
    assert second is False
    research_dir = tmp_path / "docs" / "research"
    note_files = list(research_dir.glob("*.md"))
    assert len(note_files) == 2  # INDEX.md + one note


def test_save_online_source_to_cache_handles_title_collision(tmp_path: Path) -> None:
    save_online_source_to_cache(
        title="LangGraph interrupts",
        location="https://example.com/page-a",
        summary="First source.",
        excerpt="",
        project_root=tmp_path,
        today=date(2026, 6, 15),
    )
    save_online_source_to_cache(
        title="LangGraph interrupts",
        location="https://example.com/page-b",
        summary="Second source.",
        excerpt="",
        project_root=tmp_path,
        today=date(2026, 6, 15),
    )

    research_dir = tmp_path / "docs" / "research"
    note_files = {f.name for f in research_dir.glob("langgraph-interrupts*.md")}
    assert "langgraph-interrupts.md" in note_files
    assert "langgraph-interrupts-2.md" in note_files
