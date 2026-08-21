from __future__ import annotations

from pathlib import Path

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.execution_profiles import NORMAL_TIER, SIMPLE_TIER, STRONG_TIER
from ai_tech_lead.profile_override_store import (
    ProfileOverrideError,
    ProfileOverrideStore,
    resolve_profile_with_override,
)


def _settings(**overrides):
    raw = valid_settings_dict()
    raw.update(overrides)
    return parse_settings(raw)


def test_set_override_rejects_unknown_tier(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    with pytest.raises(ProfileOverrideError):
        store.set_override(tier="nonexistent", override_tier=STRONG_TIER, calls=1, created_by="rob")


def test_set_override_rejects_zero_calls(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    with pytest.raises(ProfileOverrideError):
        store.set_override(tier=SIMPLE_TIER, override_tier=STRONG_TIER, calls=0, created_by="rob")


def test_consume_active_override_tier_returns_none_when_no_override(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    assert store.consume_active_override_tier(SIMPLE_TIER) is None


def test_override_survives_a_new_store_instance_same_db_path(tmp_path: Path) -> None:
    db_path = tmp_path / "overrides.sqlite3"
    ProfileOverrideStore(db_path).set_override(
        tier=SIMPLE_TIER, override_tier=STRONG_TIER, calls=2, created_by="rob"
    )
    # A fresh store instance against the same path stands in for a restart —
    # the override must not have been in-memory only.
    reopened = ProfileOverrideStore(db_path)
    assert reopened.consume_active_override_tier(SIMPLE_TIER) == STRONG_TIER


def test_override_expires_after_its_call_budget(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    store.set_override(tier=SIMPLE_TIER, override_tier=STRONG_TIER, calls=2, created_by="rob")

    assert store.consume_active_override_tier(SIMPLE_TIER) == STRONG_TIER
    assert store.consume_active_override_tier(SIMPLE_TIER) == STRONG_TIER
    assert store.consume_active_override_tier(SIMPLE_TIER) is None


def test_clear_override_removes_it_immediately(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    store.set_override(tier=SIMPLE_TIER, override_tier=STRONG_TIER, calls=5, created_by="rob")
    store.clear_override(SIMPLE_TIER)
    assert store.consume_active_override_tier(SIMPLE_TIER) is None


def test_list_overrides_returns_visible_active_overrides(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    store.set_override(tier=SIMPLE_TIER, override_tier=NORMAL_TIER, calls=3, created_by="rob")
    overrides = store.list_overrides()
    assert len(overrides) == 1
    assert overrides[0].tier == SIMPLE_TIER
    assert overrides[0].override_tier == NORMAL_TIER
    assert overrides[0].remaining_calls == 3
    assert overrides[0].created_by == "rob"


def test_resolve_profile_with_override_uses_active_override(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    store.set_override(tier=SIMPLE_TIER, override_tier=STRONG_TIER, calls=1, created_by="rob")

    profile = resolve_profile_with_override("request_relevance", store=store)

    assert profile.tier == STRONG_TIER
    assert profile.model == "gpt-4.1-mini"
    assert profile.reasoning_effort is None
    # Consumed — a second resolution falls back to the purpose's normal tier.
    profile_after = resolve_profile_with_override("request_relevance", store=store)
    assert profile_after.tier == SIMPLE_TIER


def test_resolve_profile_with_override_no_active_override_uses_default_tier(
    tmp_path: Path,
) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    settings = _settings(orchestrator_ai_model="gpt-4.1-mini")
    profile = resolve_profile_with_override("tech_lead_analysis", settings=settings, store=store)
    assert profile.tier == "strong"


def test_resolve_profile_with_override_unknown_purpose_raises(tmp_path: Path) -> None:
    store = ProfileOverrideStore(tmp_path / "overrides.sqlite3")
    from ai_tech_lead.execution_profiles import ExecutionProfileError

    with pytest.raises(ExecutionProfileError):
        resolve_profile_with_override("not-a-real-purpose", store=store)
