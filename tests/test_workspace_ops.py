from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import ai_tech_lead.workspace_ops as workspace_ops


def test_bootstrap_workspace_initializes_expected_paths(monkeypatch, tmp_path: Path) -> None:
    tmp_project_root = tmp_path
    tmp_data = tmp_project_root / "data"
    tmp_logs = tmp_project_root / "logs"
    tmp_settings = tmp_data / "coding_agent_settings.json"
    tmp_example = tmp_data / "coding_agent_settings.example.json"
    tmp_example.parent.mkdir(parents=True, exist_ok=True)
    tmp_example.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(workspace_ops, "PROJECT_ROOT", tmp_project_root)
    monkeypatch.setattr(workspace_ops, "DATA_DIR", tmp_data)
    monkeypatch.setattr(workspace_ops, "LOGS_DIR", tmp_logs)
    monkeypatch.setattr(workspace_ops, "SETTINGS_PATH", tmp_settings)
    monkeypatch.setattr("ai_tech_lead.config.PROJECT_ROOT", tmp_project_root)
    monkeypatch.setattr("ai_tech_lead.config.DATA_DIR", tmp_data)
    monkeypatch.setattr("ai_tech_lead.config.LOGS_DIR", tmp_logs)

    monkeypatch.setattr(
        workspace_ops,
        "load_settings",
        lambda _path: SimpleNamespace(
            project_root=str(tmp_project_root),
            backlog_path="data/backlog.sqlite3",
            knowledge_store_path="data/knowledge_store.sqlite3",
        ),
    )
    monkeypatch.setattr(
        workspace_ops, "initialize_database", lambda: tmp_data / "ai_tech_lead.sqlite3"
    )
    monkeypatch.setattr(workspace_ops, "get_checkpointer", lambda: object())

    created_backlog_paths: list[Path] = []

    class _FakeBacklogRepository:
        def __init__(self, backlog_path: Path) -> None:
            created_backlog_paths.append(backlog_path)

    monkeypatch.setattr(workspace_ops, "SqliteBacklogRepository", _FakeBacklogRepository)

    created_knowledge_paths: list[Path] = []

    class _FakeKnowledgeStore:
        def batch(self, ops):
            created_knowledge_paths.append(tmp_data / "knowledge_store.sqlite3")
            return []

    monkeypatch.setattr(
        workspace_ops,
        "get_knowledge_store",
        lambda db_path=None: _FakeKnowledgeStore(),
    )

    lines = workspace_ops.bootstrap_workspace(settings_path=tmp_settings)

    assert tmp_settings.exists()
    assert created_backlog_paths == [tmp_data / "backlog.sqlite3"]
    assert created_knowledge_paths == [tmp_data / "knowledge_store.sqlite3"]
    assert any("Initialised app database" in line for line in lines)


def test_doctor_workspace_reports_health(monkeypatch, tmp_path: Path) -> None:
    tmp_project_root = tmp_path
    tmp_data = tmp_project_root / "data"
    tmp_logs = tmp_project_root / "logs"
    tmp_settings = tmp_data / "coding_agent_settings.json"
    tmp_example = tmp_data / "coding_agent_settings.example.json"
    tmp_docs = tmp_project_root / "docs"
    tmp_data.mkdir(parents=True, exist_ok=True)
    tmp_logs.mkdir(parents=True, exist_ok=True)
    tmp_docs.mkdir(parents=True, exist_ok=True)
    tmp_settings.write_text("{}", encoding="utf-8")
    tmp_example.write_text("{}", encoding="utf-8")
    (tmp_docs / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    (tmp_data / "prompts.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(workspace_ops, "PROJECT_ROOT", tmp_project_root)
    monkeypatch.setattr(workspace_ops, "DATA_DIR", tmp_data)
    monkeypatch.setattr(workspace_ops, "LOGS_DIR", tmp_logs)
    monkeypatch.setattr(workspace_ops, "SETTINGS_PATH", tmp_settings)

    monkeypatch.setattr(
        workspace_ops,
        "load_settings",
        lambda _path: SimpleNamespace(
            project_root=str(tmp_project_root),
            backlog_path="data/backlog.sqlite3",
            knowledge_store_path="data/knowledge_store.sqlite3",
        ),
    )
    monkeypatch.setattr(workspace_ops, "_is_git_tracked", lambda _path: False)

    report = workspace_ops.doctor_workspace(settings_path=tmp_settings)

    assert report.ok is True
    assert any("Settings file present" in line for line in report.lines)
    assert any("Prompt registry present" in line for line in report.lines)
