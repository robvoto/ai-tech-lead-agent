from __future__ import annotations

from pathlib import Path

from langchain_core.runnables.graph import Edge, Graph, Node

from ai_tech_lead import graph_diagrams
from ai_tech_lead.coding_workflow_graph import NodeName


def test_export_graph_diagrams_writes_coding_graph(monkeypatch, tmp_path: Path) -> None:
    coding_path = tmp_path / "graph_diagram.png"

    class _FakeGraph:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._mermaid = mermaid
            self._payload = payload
            self.mermaid_calls = 0
            self.png_calls = 0

        def draw_mermaid(self, **_kwargs) -> str:
            self.mermaid_calls += 1
            return self._mermaid

        def draw_mermaid_png(self, **_kwargs) -> bytes:
            self.png_calls += 1
            return self._payload

    class _FakeApp:
        def __init__(self, mermaid: str, payload: bytes) -> None:
            self._graph = _FakeGraph(mermaid, payload)

        def get_graph(self) -> _FakeGraph:
            return self._graph

    monkeypatch.setattr(graph_diagrams, "GRAPH_DIAGRAM_PATH", coding_path)
    monkeypatch.setattr(graph_diagrams, "ensure_project_dirs", lambda: None)
    monkeypatch.setattr(graph_diagrams, "_presentation_graph", lambda graph: graph)
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

        def draw_mermaid(self, **_kwargs) -> str:
            return self._mermaid

        def draw_mermaid_png(self, **_kwargs) -> bytes:
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
    monkeypatch.setattr(graph_diagrams, "_presentation_graph", lambda graph: graph)
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

        def draw_mermaid(self, **_kwargs) -> str:
            idx = min(call_count[0], len(mermaid_versions) - 1)
            call_count[0] += 1
            return mermaid_versions[idx]

        def draw_mermaid_png(self, **_kwargs) -> bytes:
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
    monkeypatch.setattr(graph_diagrams, "_presentation_graph", lambda graph: graph)
    monkeypatch.setattr(graph_diagrams, "build_graph", lambda **_kw: _FakeApp(coding_graph))

    graph_diagrams.export_graph_diagrams()
    graph_diagrams.export_graph_diagrams()

    assert coding_graph.png_calls == 2


def test_presentation_graph_collapses_run_budget_fan_in_and_resume_edges() -> None:
    budget_node_id = NodeName.RUN_BUDGET_INTERRUPT.value
    source_a = Node(id="source_a", name="Source A", data=None, metadata=None)
    source_b = Node(id="source_b", name="Source B", data=None, metadata=None)
    budget = Node(id=budget_node_id, name=budget_node_id, data=None, metadata=None)
    end = Node(id="end", name="End", data=None, metadata=None)
    runtime_graph = Graph(
        nodes={node.id: node for node in (source_a, source_b, budget, end)},
        edges=[
            Edge(source="source_a", target="source_b"),
            Edge(source="source_a", target=budget_node_id, data="budget exceeded", conditional=True),
            Edge(source="source_b", target=budget_node_id, data="budget exceeded", conditional=True),
            Edge(source=budget_node_id, target="source_a", conditional=True),
            Edge(source=budget_node_id, target="source_b", conditional=True),
            Edge(source=budget_node_id, target="end", data="end", conditional=True),
        ],
    )

    presentation = graph_diagrams._presentation_graph(runtime_graph)

    assert len(runtime_graph.edges) == 6  # executable graph is untouched
    assert len(presentation.edges) == 3
    assert presentation.nodes[budget_node_id].name == graph_diagrams.RUN_BUDGET_INTERRUPT_LABEL
    assert (
        presentation.nodes[graph_diagrams.RUN_BUDGET_SCOPE_NODE_ID].name
        == graph_diagrams.RUN_BUDGET_SCOPE_LABEL
    )
    budget_edges = [
        edge
        for edge in presentation.edges
        if budget_node_id in {edge.source, edge.target}
    ]
    assert [(edge.source, edge.target, edge.data) for edge in budget_edges] == [
        (graph_diagrams.RUN_BUDGET_SCOPE_NODE_ID, budget_node_id, "budget exceeded"),
        (budget_node_id, graph_diagrams.RUN_BUDGET_SCOPE_NODE_ID, "retry blocked step / stop"),
    ]
