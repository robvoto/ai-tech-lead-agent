from __future__ import annotations

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.backlog_draft_builder import build_backlog_refinement_from_text
from ai_tech_lead.backlog_repository import MarkdownBacklogRepository
from ai_tech_lead.research_cache import ResearchCacheEntry


def test_build_backlog_refinement_from_text_uses_cache_and_ai(monkeypatch, tmp_path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n## ATL-001 - Existing\n\nGoal:\nExisting\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)
    cache_entry = ResearchCacheEntry(
        path=tmp_path / "docs" / "research" / "backlog-refinement.md",
        title="Backlog refinement with cache-first research",
        summary="Cache-first backlog refinement with structured output.",
        body="## Summary\nCache-first backlog refinement.",
        sources=["https://docs.langchain.com/oss/python/langchain/structured-output"],
        refreshed_on=None,
        freshness_risk="Medium: no refresh date.",
    )
    cache_calls: list[bool] = []
    prompts: list[str] = []

    monkeypatch.setattr(
        "ai_tech_lead.backlog_draft_builder.load_research_cache_entries",
        lambda: cache_calls.append(True) or [cache_entry],
    )

    class FakeResult:
        text = (
            "{"
            '"title": "Improve admin UI", '
            '"item_type": "Story", '
            '"epic": "Admin UI", '
            '"priority": "High", '
            '"size": "S", '
            '"approval_required": true, '
            '"approval_reason": "Changes local UI behaviour.", '
            '"problem": "The admin UI is hard to scan.", '
            '"desired_outcome": "Make the admin UI easier to use.", '
            '"scope": ["Improve layout clarity", "Keep current settings model"], '
            '"out_of_scope": ["Rewrite the admin UI in a framework"], '
            '"acceptance_criteria": ["The page is easier to read", "Settings still save"], '
            '"duplicate_check_result": "No duplicate found.", '
            '"stale_check_result": "No stale item found.", '
            '"already_done_check_result": "Not already done.", '
            '"research_required": true, '
            '"research_cache_used": ['
            '"docs/research/backlog-refinement-implementation-patterns.md"], '
            '"external_research_needed": false, '
            '"recommended_implementation_pattern": '
            '"Use a schema-validated backlog refinement flow.", '
            '"patterns_explicitly_rejected": '
            '["Freeform prose draft", "Heuristic duplicate matching"], '
            '"freshness_risk": "Low.", '
            '"implementation_guidance": "Check the cache first and keep the item structured.", '
            '"approval_risk_flags": ["Touches local settings and UI"]'
            "}"
        )

    def fake_llm(*, prompt, config):
        prompts.append(prompt)
        return FakeResult()

    monkeypatch.setattr("ai_tech_lead.backlog_draft_builder.call_orchestrator_llm", fake_llm)

    result = build_backlog_refinement_from_text(
        text="make the admin easier",
        repository=repository,
        settings=settings,
    )

    assert cache_calls == [True]
    assert prompts and "Cached research notes:" in prompts[0]
    assert '"priority": string' in prompts[0]
    assert "High, Medium, Low" in prompts[0]
    assert '"item_type": string' in prompts[0]
    assert "Story, Task, Chore, Bug" in prompts[0]
    assert '"size": string' in prompts[0]
    assert "XS, S, M, L, XL" in prompts[0]
    assert "Repository-managed fields" in prompts[0]
    assert "Existing" in prompts[0]
    assert result.draft.item_id == "ATL-002"
    assert result.draft.title == "Improve admin UI"
    assert result.draft.creator == "Human"
    assert result.draft.item_type == "Story"
    assert result.draft.epic == "Admin UI"
    assert result.draft.priority == "High"
    assert result.draft.size == "S"
    assert result.draft.approval_required is True
    assert result.draft.research_required is True
    assert result.draft.external_research_needed is False
    assert len(result.draft.scope) == 2
