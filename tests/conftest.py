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
def _isolate_approval_store(tmp_path, monkeypatch):
    """Redirect the approval token store to a per-test temp path."""
    import ai_tech_lead.approval_store as ap_mod

    monkeypatch.setattr(ap_mod, "APPROVALS_DB", tmp_path / "pending_approvals.sqlite3")
