from __future__ import annotations

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.backlog_draft_builder import build_backlog_draft_from_text
from ai_tech_lead.backlog_repository import MarkdownBacklogRepository

from helpers import valid_settings_dict


def test_build_backlog_draft_from_text_uses_ai(monkeypatch, tmp_path) -> None:
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text(
        "# Backlog\n\n"
        "## ATL-001 - Existing\n\n"
        "Goal:\nExisting\n",
        encoding="utf-8",
    )
    repository = MarkdownBacklogRepository(backlog_path)
    raw_settings = valid_settings_dict()
    raw_settings["orchestrator_ai_enabled"] = True
    settings = parse_settings(raw_settings)

    class FakeResult:
        text = '{"title": "Improve admin UI", "approval_required": true, "approval_reason": "Changes local UI behaviour.", "goal": "Make the admin UI easier to use.", "constraints": ["Keep HTML CSS and JS separated", "Do not change graph behaviour"]}'

    monkeypatch.setattr(
        "ai_tech_lead.backlog_draft_builder.call_orchestrator_llm",
        lambda prompt, config: FakeResult(),
    )

    result = build_backlog_draft_from_text(
        text="make the admin easier",
        repository=repository,
        settings=settings,
    )

    assert result.draft.item_id == "ATL-002"
    assert result.draft.title == "Improve admin UI"
    assert result.draft.approval_required is True
    assert len(result.draft.constraints) == 2
