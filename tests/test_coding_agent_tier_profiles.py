"""Tests for the per-backend coding-agent effort tier resolver (ATL-034)."""

from __future__ import annotations

import json

import ai_tech_lead.coding_agent_tier_profiles as tier_profiles_mod
from ai_tech_lead.coding_agent_tier_profiles import resolve_tier_args


def _reset_cache(monkeypatch) -> None:
    monkeypatch.setattr(tier_profiles_mod, "_registry_cache", None)


def _write_registry(tmp_path, payload: dict) -> None:
    (tmp_path / "coding_agent_tier_profiles.json").write_text(json.dumps(payload))


def test_resolve_tier_args_returns_configured_flags(tmp_path, monkeypatch) -> None:
    _reset_cache(monkeypatch)
    _write_registry(
        tmp_path,
        {"backends": {"codex": {"light": ["-c", "model_reasoning_effort=low"]}}},
    )
    monkeypatch.setattr(
        tier_profiles_mod,
        "CODING_AGENT_TIER_PROFILES_PATH",
        tmp_path / "coding_agent_tier_profiles.json",
    )

    args = resolve_tier_args("codex", "light")

    assert args == ("-c", "model_reasoning_effort=low")


def test_resolve_tier_args_unconfigured_backend_returns_no_args(tmp_path, monkeypatch) -> None:
    _reset_cache(monkeypatch)
    _write_registry(tmp_path, {"backends": {"codex": {"light": ["-c", "x"]}}})
    monkeypatch.setattr(
        tier_profiles_mod,
        "CODING_AGENT_TIER_PROFILES_PATH",
        tmp_path / "coding_agent_tier_profiles.json",
    )

    args = resolve_tier_args("claude", "light")

    assert args == ()


def test_resolve_tier_args_unconfigured_tier_for_known_backend_returns_no_args(
    tmp_path, monkeypatch
) -> None:
    _reset_cache(monkeypatch)
    _write_registry(tmp_path, {"backends": {"codex": {"light": ["-c", "x"]}}})
    monkeypatch.setattr(
        tier_profiles_mod,
        "CODING_AGENT_TIER_PROFILES_PATH",
        tmp_path / "coding_agent_tier_profiles.json",
    )

    args = resolve_tier_args("codex", "deep")

    assert args == ()


def test_resolve_tier_args_unknown_tier_name_returns_no_args(tmp_path, monkeypatch) -> None:
    _reset_cache(monkeypatch)
    _write_registry(tmp_path, {"backends": {"codex": {"light": ["-c", "x"]}}})
    monkeypatch.setattr(
        tier_profiles_mod,
        "CODING_AGENT_TIER_PROFILES_PATH",
        tmp_path / "coding_agent_tier_profiles.json",
    )

    args = resolve_tier_args("codex", "not_a_real_tier")

    assert args == ()


def test_real_config_file_resolves_codex_tiers() -> None:
    """Guards the actual shipped config, not just a temp fixture."""

    for tier in ("light", "standard", "deep"):
        args = resolve_tier_args("codex", tier)
        assert args, f"expected configured args for codex/{tier}"
