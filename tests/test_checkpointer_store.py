from __future__ import annotations

import os

import ai_tech_lead.checkpointer_store as checkpointer_store


def test_checkpointer_enables_strict_msgpack_before_initialization(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LANGGRAPH_STRICT_MSGPACK", raising=False)
    monkeypatch.setattr(checkpointer_store, "_CHECKPOINT_DB", tmp_path / "checkpoints.sqlite3")
    monkeypatch.setattr(checkpointer_store, "_conn", None)
    monkeypatch.setattr(checkpointer_store, "_checkpointer", None)

    checkpointer_store.get_checkpointer()

    assert os.environ["LANGGRAPH_STRICT_MSGPACK"] == "true"
