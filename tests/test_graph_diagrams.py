from __future__ import annotations

from pathlib import Path

from ai_tech_lead import graph_diagrams


def test_export_graph_diagrams_writes_coding_graph(monkeypatch, tmp_path: Path) -> None:
    coding_path = tmp_path / "graph_diagram.png"

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
    monkeypatch.setattr(graph_diagrams, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(
        graph_diagrams, "build_graph", lambda **_kw: _FakeApp("coding-mermaid", b"coding-png")
    )

    paths = graph_diagrams.export_graph_diagrams()

    assert paths == [coding_path]
    assert coding_path.read_bytes() == b"coding-png"


def test_export_graph_diagrams_skips_when_unchanged(monkeypatch, tmp_path: Path) -> None:
    coding_path = tmp_path / "graph_diagram.png"

    class _FakeGraph:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._mermaid = mermaid
            self._payload = payload
            self.png_calls = 0

        def draw_mermaid(self) -> str:
            return self._mermaid

        def draw_mermaid_png(self) -> bytes:
            self.png_calls += 1
            return self._payload

    class _FakeApp:
        def __init__(self, graph: _FakeGraph) -> None:
            self._graph = graph

        def get_graph(self) -> _FakeGraph:
            return self._graph

    coding_graph = _FakeGraph("coding-mermaid", b"coding-png")
    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_PATH", coding_path)
    monkeypatch.setattr(graph_diagrams, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(
        graph_diagrams, "build_graph", lambda **_kw: _FakeApp(coding_graph)
    )

    first_paths = graph_diagrams.export_graph_diagrams()
    second_paths = graph_diagrams.export_graph_diagrams()

    assert first_paths == [coding_path]
    assert second_paths == []  # unchanged — skipped
    assert coding_graph.png_calls == 1


def test_export_graph_diagrams_rewrites_when_changed(monkeypatch, tmp_path: Path) -> None:
    coding_path = tmp_path / "graph_diagram.png"

    mermaid_versions = ["version-1", "version-2"]
    call_count = [0]

    class _FakeGraph:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload
            self.png_calls = 0

        def draw_mermaid(self) -> str:
            idx = min(call_count[0], len(mermaid_versions) - 1)
            call_count[0] += 1
            return mermaid_versions[idx]

        def draw_mermaid_png(self) -> bytes:
            self.png_calls += 1
            return self._payload

    class _FakeApp:
        def __init__(self, graph: _FakeGraph) -> None:
            self._graph = graph

        def get_graph(self) -> _FakeGraph:
            return self._graph

    coding_graph = _FakeGraph(b"coding-png")

    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_PATH", coding_path)
    monkeypatch.setattr(graph_diagrams, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(graph_diagrams, "build_graph", lambda **_kw: _FakeApp(coding_graph))

    graph_diagrams.export_graph_diagrams()
    graph_diagrams.export_graph_diagrams()

    assert coding_graph.png_calls == 2
