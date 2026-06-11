from __future__ import annotations

from pathlib import Path

from ai_tech_lead import backlog_graph_runner


def test_save_graph_diagram_writes_to_data_path(monkeypatch, tmp_path: Path) -> None:
    output_path = tmp_path / "graph_diagram.png"

    class _FakeGraph:
        def draw_mermaid_png(self) -> bytes:
            return b"fake-png"

    class _FakeApp:
        def get_graph(self) -> _FakeGraph:
            return _FakeGraph()

    monkeypatch.setattr(backlog_graph_runner, "GRAPH_DIAGRAM_PATH", output_path)
    monkeypatch.setattr(backlog_graph_runner, "ensure_project_dirs", lambda: None)

    backlog_graph_runner.save_graph_diagram(_FakeApp())

    assert output_path.read_bytes() == b"fake-png"
