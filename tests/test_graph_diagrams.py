from __future__ import annotations

from pathlib import Path

from ai_tech_lead import graph_diagrams


def test_export_graph_diagrams_writes_both_graphs(monkeypatch, tmp_path: Path) -> None:
    coding_path = tmp_path / "graph_diagram.png"
    telegram_path = tmp_path / "telegram_agent_graph.png"
    signatures_path = tmp_path / "graph_diagram_signatures.json"

    class _FakeGraph:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._mermaid = mermaid
            self._payload = payload
            self.mermaid_calls = 0
            self.png_calls = 0

        def draw_mermaid(self) -> str:
            self.mermaid_calls += 1
            return self._mermaid

        def draw_mermaid_png(self) -> bytes:
            self.png_calls += 1
            return self._payload

    class _FakeApp:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._graph = _FakeGraph(mermaid, payload)

        def get_graph(self) -> _FakeGraph:
            return self._graph

    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_PATH", coding_path)
    monkeypatch.setattr(graph_diagrams, "TELEGRAM_AGENT_GRAPH_DIAGRAM_PATH", telegram_path)
    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_SIGNATURES_PATH", signatures_path)
    monkeypatch.setattr(graph_diagrams, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(
        graph_diagrams, "build_graph", lambda **_kw: _FakeApp("coding-mermaid", b"coding-png")
    )
    monkeypatch.setattr(
        graph_diagrams,
        "build_telegram_agent_graph",
        lambda **_kw: _FakeApp("telegram-mermaid", b"telegram-png"),
    )
    monkeypatch.setattr(graph_diagrams, "load_settings", lambda: object())

    paths = graph_diagrams.export_graph_diagrams()

    assert paths == [coding_path, telegram_path]
    assert coding_path.read_bytes() == b"coding-png"
    assert telegram_path.read_bytes() == b"telegram-png"
    assert signatures_path.exists()


def test_export_graph_diagrams_skips_unchanged_graphs(monkeypatch, tmp_path: Path) -> None:
    coding_path = tmp_path / "graph_diagram.png"
    telegram_path = tmp_path / "telegram_agent_graph.png"
    signatures_path = tmp_path / "graph_diagram_signatures.json"

    class _FakeGraph:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._mermaid = mermaid
            self._payload = payload
            self.mermaid_calls = 0
            self.png_calls = 0

        def draw_mermaid(self) -> str:
            self.mermaid_calls += 1
            return self._mermaid

        def draw_mermaid_png(self) -> bytes:
            self.png_calls += 1
            return self._payload

    class _FakeApp:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._graph = _FakeGraph(mermaid, payload)

        def get_graph(self) -> _FakeGraph:
            return self._graph

    coding_app = _FakeApp("coding-mermaid", b"coding-png")
    telegram_app = _FakeApp("telegram-mermaid", b"telegram-png")
    build_calls: list[str] = []

    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_PATH", coding_path)
    monkeypatch.setattr(graph_diagrams, "TELEGRAM_AGENT_GRAPH_DIAGRAM_PATH", telegram_path)
    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_SIGNATURES_PATH", signatures_path)
    monkeypatch.setattr(graph_diagrams, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(
        graph_diagrams, "build_graph", lambda **_kw: build_calls.append("coding") or coding_app
    )
    monkeypatch.setattr(
        graph_diagrams,
        "build_telegram_agent_graph",
        lambda **_kw: build_calls.append("telegram") or telegram_app,
    )
    monkeypatch.setattr(graph_diagrams, "load_settings", lambda: object())

    first_paths = graph_diagrams.export_graph_diagrams()
    second_paths = graph_diagrams.export_graph_diagrams()

    assert first_paths == [coding_path, telegram_path]
    assert second_paths == []
    assert coding_app.get_graph().png_calls == 1
    assert telegram_app.get_graph().png_calls == 1
    assert signatures_path.exists()
