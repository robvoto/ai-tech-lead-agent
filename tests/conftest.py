"""Test isolation fixtures — ensure tests never write to permanent data stores."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_checkpointer(tmp_path, monkeypatch):
    """Redirect the SqliteSaver checkpointer to a per-test temp path."""
    import ai_tech_lead.checkpointer_store as cs_mod

    monkeypatch.setattr(cs_mod, "_CHECKPOINT_DB", tmp_path / "checkpoints.sqlite3")
    monkeypatch.setattr(cs_mod, "_conn", None)
    monkeypatch.setattr(cs_mod, "_checkpointer", None)


@pytest.fixture(autouse=True)
def _isolate_backlog_runtime_store(tmp_path, monkeypatch):
    """Redirect the SQLite backlog runtime store to a per-test temp path."""
    import ai_tech_lead.backlog_runtime_store as brs_mod

    monkeypatch.setattr(brs_mod, "BACKLOG_DB_PATH", tmp_path / "backlog.sqlite3")


@pytest.fixture(autouse=True)
def _isolate_knowledge_store(tmp_path, monkeypatch):
    """Redirect the knowledge store to a per-test temp path."""
    import ai_tech_lead.knowledge_store as ks_mod

    monkeypatch.setattr(ks_mod, "_DEFAULT_STORE_PATH", tmp_path / "knowledge_store.sqlite3")
    monkeypatch.setattr(ks_mod, "_store", None)


@pytest.fixture(autouse=True)
def _disable_relevance_check_ai_by_default(monkeypatch):
    """Default the ATL-relevance and code-look checks to AI-disabled (fail open).

    Without this, every test that runs the full graph would make real
    network calls on the first two nodes. Tests that specifically want to
    exercise the AI path can still monkeypatch it themselves afterward.
    """
    from helpers import valid_settings_dict

    import ai_tech_lead.app_settings as settings_mod
    import ai_tech_lead.code_look_checker as code_look_mod
    import ai_tech_lead.request_relevance as relevance_mod

    settings = settings_mod.parse_settings(valid_settings_dict())
    monkeypatch.setattr(relevance_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(code_look_mod, "load_settings", lambda: settings)
