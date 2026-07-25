from __future__ import annotations

import logging
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


def test_load_research_cache_entries_reads_index_and_note(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
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
    assert "Research cache loaded: 1 note(s)" in caplog.text


def _make_fake_source(title: str, location: str = "/fake/path.md") -> ResearchSource:
    return ResearchSource(
        title=title,
        location=location,
        summary="Fake summary.",
        excerpt="",
        source_kind="local-doc",
    )


def test_check_research_requirements_no_gap_skips_cache(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (False, "", "No external fact required for this typo fix."),
    )

    result = check_research_requirements("Fix typo in README", settings)

    assert result.has_gap is False
    assert result.online_research_needed is False
    assert result.sources_found == 0


def test_check_research_requirements_large_task_with_no_gap_skips_cache(monkeypatch) -> None:
    """A task can be architecturally significant yet need no external research."""

    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (
            False,
            "",
            "Large refactor, but everything needed is already known from the codebase.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local cache should not be searched when no gap was identified")
        ),
    )

    result = check_research_requirements(
        "Refactor the entire backlog runtime storage layer", settings
    )

    assert result.has_gap is False
    assert result.online_research_needed is False


def test_check_research_requirements_small_task_with_real_gap_checks_cache(monkeypatch) -> None:
    """A small task can still hinge on one unfamiliar external fact."""

    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (
            True,
            "What are Telegram Bot API's official rate-limit and retry rules?",
            "Retry behaviour must match Telegram's documented rules.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda _gap_question, _settings: [
            _make_fake_source("LangGraph approval"),
            _make_fake_source("Backlog patterns"),
        ],
    )

    result = check_research_requirements("Add retry handling to the Telegram client", settings)

    assert result.has_gap is True
    assert result.gap_question == "What are Telegram Bot API's official rate-limit and retry rules?"
    assert result.sources_found == 2
    assert result.online_research_needed is False


def test_check_research_requirements_searches_cache_by_gap_question_not_raw_request(
    monkeypatch,
) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)
    seen_queries: list[str] = []

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (
            True,
            "What are Telegram Bot API's official rate-limit and retry rules?",
            "Retry behaviour must match Telegram's documented rules.",
        ),
    )

    def fake_collect_local_research_sources(query, _settings):
        seen_queries.append(query)
        return []

    monkeypatch.setattr(
        "ai_tech_lead.research_checker.collect_local_research_sources",
        fake_collect_local_research_sources,
    )

    check_research_requirements("Add retry handling to the Telegram client", settings)

    assert seen_queries == ["What are Telegram Bot API's official rate-limit and retry rules?"]


def test_check_research_requirements_gap_with_one_source_triggers_gate(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (
            True,
            "What are LangGraph's official interrupt semantics?",
            "Involves LangGraph interrupt nodes.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda _gap_question, _settings: [_make_fake_source("LangGraph approval")],
    )

    result = check_research_requirements("Add CLARIFICATION_GATE to workflow", settings)

    assert result.has_gap is True
    assert result.sources_found == 1
    assert result.online_research_needed is True


def test_check_research_requirements_ai_disabled_requires_approval() -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=False)

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.has_gap is True
    assert result.online_research_needed is True
    assert "requiring human approval" in result.gap_reason.lower()


def test_check_research_requirements_llm_unavailable_requires_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (_ for _ in ()).throw(OrchestratorLlmError("offline")),
    )

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.has_gap is True
    assert result.online_research_needed is True
    assert "unavailable" in result.gap_reason.lower()


def test_check_research_requirements_invalid_llm_response_requires_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (_ for _ in ()).throw(ValueError("bad payload")),
    )

    result = check_research_requirements("Add interrupt gate to LangGraph workflow", settings)

    assert result.has_gap is True
    assert result.online_research_needed is True
    assert "invalid" in result.gap_reason.lower()


def test_check_research_requirements_online_not_triggered_without_approval(monkeypatch) -> None:
    settings = replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)

    monkeypatch.setattr(
        "ai_tech_lead.research_checker._llm_check_knowledge_gap",
        lambda _req, _settings, _code_context: (
            True,
            "What does LangGraph persistence require for checkpointing?",
            "Complex: LangGraph persistence.",
        ),
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_checker.collect_local_research_sources",
        lambda _gap_question, _settings: [],
    )

    result = check_research_requirements("Add checkpointing to workflow", settings)

    assert result.online_research_needed is True
    assert result.has_gap is True
    assert result.sources_found == 0


def test_save_online_source_to_cache_writes_note_and_index(tmp_path: Path) -> None:
    research_dir = tmp_path / "docs" / "research"

    saved = save_online_source_to_cache(
        title="LangGraph interrupts",
        location="https://docs.langchain.com/oss/python/langgraph/interrupts",
        summary="Interrupts pause graph execution and resume with Command.",
        excerpt="",
        project_root=tmp_path,
        question="What are LangGraph's official interrupt semantics?",
        today=date(2026, 6, 15),
    )

    assert saved is True
    note_path = research_dir / "langgraph-interrupts.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "topic: LangGraph interrupts" in content
    assert "date: 2026-06-15" in content
    assert "question: What are LangGraph's official interrupt semantics?" in content
    assert "https://docs.langchain.com/oss/python/langgraph/interrupts" in content
    assert "## Summary" in content
    assert "Interrupts pause graph execution" in content

    index_content = (research_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "langgraph-interrupts.md" in index_content
    assert "Interrupts pause graph execution" in index_content

    entries = load_research_cache_entries(index_path=research_dir / "INDEX.md", today=date(2026, 6, 15))
    assert entries[0].question == "What are LangGraph's official interrupt semantics?"


def test_save_online_source_to_cache_question_is_optional(tmp_path: Path) -> None:
    saved = save_online_source_to_cache(
        title="Backlog patterns",
        location="https://docs.langchain.com/oss/python/langchain/structured-output",
        summary="Use structured output for backlog drafts.",
        excerpt="",
        project_root=tmp_path,
        today=date(2026, 6, 15),
    )

    assert saved is True
    note_path = tmp_path / "docs" / "research" / "backlog-patterns.md"
    content = note_path.read_text(encoding="utf-8")
    assert "question:" not in content


def test_save_online_source_to_cache_skips_duplicate_url(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
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
    assert "Reusing cached online source" in caplog.text
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
