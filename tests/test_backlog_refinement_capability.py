from __future__ import annotations

from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.backlog_refinement_capability import (
    BacklogRefinementProposal,
    infer_backlog_item_prefix,
    prepare_backlog_refinement_proposal,
)
from ai_tech_lead.backlog_repository import BacklogItem, BacklogRefinementDraft
from ai_tech_lead.backlog_status import BacklogStatus


class _FakeRepository:
    def __init__(self, items: list[BacklogItem]) -> None:
        self._items = items

    def list_items(self) -> list[BacklogItem]:
        return list(self._items)

    def next_item_id(self, prefix: str = "ATL") -> str:
        highest = 0
        for item in self._items:
            if item.item_id.startswith(prefix + "-"):
                highest = max(highest, int(item.item_id.split("-")[1]))
        return f"{prefix}-{highest + 1:03d}"

    def add_refined_item(self, draft: BacklogRefinementDraft):
        return draft


def _settings():
    return replace(parse_settings(valid_settings_dict()), orchestrator_ai_enabled=True)


def test_prepare_backlog_refinement_proposal_loads_skill_and_uses_configured_prefix(
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}
    repository = _FakeRepository(
        [BacklogItem(item_id="HUB-001", title="Existing item", body="", status=BacklogStatus.BACKLOG)]
    )

    monkeypatch.setattr(
        "ai_tech_lead.backlog_refinement_capability.load_backlog_item_authoring_skill",
        lambda: "Use the backlog-item-authoring skill rules.",
    )

    def fake_build_backlog_refinement_from_text(**kwargs):
        captured["item_id_prefix"] = kwargs["item_id_prefix"]
        captured["skill_instructions"] = kwargs["skill_instructions"]
        return type(
            "_BuildResult",
            (),
            {
                "draft": BacklogRefinementDraft(
                    item_id="HUB-002",
                    title="Add backlog-only mode",
                    creator="Human",
                    item_type="Story",
                    epic="Backlog",
                    priority="High",
                    size="M",
                    approval_required=True,
                    approval_reason="Writes backlog items.",
                    problem="Need backlog-only mode.",
                    desired_outcome="Hub can refine backlog items.",
                    scope=["Hub backlog refinement"],
                    out_of_scope=["Coding workflow changes"],
                    acceptance_criteria=["Hub can request a draft"],
                    duplicate_check_result="No duplicate found.",
                    stale_check_result="No stale item found.",
                    already_done_check_result="Not already done.",
                    research_required=False,
                    research_cache_used=[],
                    external_research_needed=False,
                    recommended_implementation_pattern="Reuse existing service.",
                    patterns_explicitly_rejected=["Second workflow"],
                    freshness_risk="Low.",
                    implementation_guidance="Keep it bounded.",
                    approval_risk_flags=["Writes backlog items"],
                ),
                "source": "fake-model",
            },
        )()

    monkeypatch.setattr(
        "ai_tech_lead.backlog_refinement_capability.build_backlog_refinement_from_text",
        fake_build_backlog_refinement_from_text,
    )

    proposal = prepare_backlog_refinement_proposal(
        text="Add backlog-only mode",
        repository=repository,
        settings=_settings(),
        item_id_prefix="HUB",
    )

    assert captured["item_id_prefix"] == "HUB"
    assert "backlog-item-authoring skill rules" in captured["skill_instructions"]
    assert proposal.draft.item_id == "HUB-002"


def test_prepare_backlog_refinement_proposal_marks_duplicate_like_match_blocking(
    monkeypatch,
) -> None:
    repository = _FakeRepository(
        [
            BacklogItem(
                item_id="ATL-001",
                title="Add backlog-only mode",
                body="Scope:\nHub backlog refinement",
                status=BacklogStatus.BACKLOG,
            )
        ]
    )

    monkeypatch.setattr(
        "ai_tech_lead.backlog_refinement_capability.load_backlog_item_authoring_skill",
        lambda: "skill",
    )
    monkeypatch.setattr(
        "ai_tech_lead.backlog_refinement_capability.build_backlog_refinement_from_text",
        lambda **_kwargs: type(
            "_BuildResult",
            (),
            {
                "draft": BacklogRefinementDraft(
                    item_id="ATL-002",
                    title="Add backlog-only mode",
                    creator="Human",
                    item_type="Story",
                    epic="Backlog",
                    priority="High",
                    size="M",
                    approval_required=True,
                    approval_reason="Writes backlog items.",
                    problem="Need backlog-only mode.",
                    desired_outcome="Hub can refine backlog items.",
                    scope=["Hub backlog refinement"],
                    out_of_scope=["Coding workflow changes"],
                    acceptance_criteria=["Hub can request a draft"],
                    duplicate_check_result="Potential duplicate found.",
                    stale_check_result="No stale item found.",
                    already_done_check_result="Not already done.",
                    research_required=False,
                    research_cache_used=[],
                    external_research_needed=False,
                    recommended_implementation_pattern="Reuse existing service.",
                    patterns_explicitly_rejected=["Second workflow"],
                    freshness_risk="Low.",
                    implementation_guidance="Keep it bounded.",
                    approval_risk_flags=["Writes backlog items"],
                ),
                "source": "fake-model",
            },
        )(),
    )

    proposal = prepare_backlog_refinement_proposal(
        text="Add backlog-only mode",
        repository=repository,
        settings=_settings(),
        item_id_prefix="ATL",
    )

    assert proposal.blocked is True
    assert proposal.matches[0].item_id == "ATL-001"
    assert proposal.matches[0].kind == "likely_duplicate"


def test_infer_backlog_item_prefix_requires_one_unambiguous_prefix() -> None:
    repository = _FakeRepository(
        [BacklogItem(item_id="HUB-001", title="Existing item", body="", status=BacklogStatus.BACKLOG)]
    )

    assert infer_backlog_item_prefix(repository) == "HUB"
