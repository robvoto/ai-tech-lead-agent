from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.research_sources import (
    collect_local_research_sources,
    collect_online_research_sources,
)


def test_collect_local_research_sources_uses_local_indexes(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    docs_dir = tmp_path / "docs"
    research_dir = docs_dir / "research"
    research_dir.mkdir(parents=True)

    doc_path = docs_dir / "ARCHITECTURE.md"
    doc_path.write_text(
        "# Architecture\n\n"
        "## Summary\n"
        "LangGraph interrupts and checkpointers should stay separate.\n",
        encoding="utf-8",
    )
    research_note_path = research_dir / "langgraph-hard-rules.md"
    research_note_path.write_text(
        "---\n"
        "topic: LangGraph hard rules vs LLM-based approval gates\n"
        "date: 2026-06-10\n"
        "sources:\n"
        "  - https://docs.langchain.com/oss/python/langgraph/interrupts\n"
        "---\n\n"
        "## Summary\n"
        "Approval gates should use explicit interrupts and a separate retrieval node.\n",
        encoding="utf-8",
    )
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n"
        "## Core project documents\n\n"
        "- `ARCHITECTURE.md` - LangGraph interrupts and checkpointers.\n",
        encoding="utf-8",
    )
    (research_dir / "INDEX.md").write_text(
        "# Research Cache Index\n\n"
        "## LangGraph / Workflow\n\n"
        "- [langgraph-hard-rules.md](langgraph-hard-rules.md) — "
        "LangGraph hard rules vs LLM-based approval gates (2026-06-10)\n",
        encoding="utf-8",
    )

    settings = replace(
        parse_settings(valid_settings_dict()),
        project_root=str(tmp_path),
        research_local_index_paths=["docs/INDEX.md", "docs/research/INDEX.md"],
        research_max_local_sources=2,
    )

    sources = collect_local_research_sources("LangGraph interrupts and checkpointers", settings)

    assert {source.title for source in sources} == {
        "ARCHITECTURE.md",
        "LangGraph hard rules vs LLM-based approval gates",
    }
    assert {source.location for source in sources} == {
        "docs/ARCHITECTURE.md",
        "docs/research/langgraph-hard-rules.md",
    }
    assert "Local research selection: selected 2 source(s)" in caplog.text
    assert "1 local-doc" in caplog.text
    assert "1 local-research" in caplog.text
    assert "Local research selection request terms" in caplog.text
    assert "Local docs candidate: score=" in caplog.text
    assert "Local research cache candidate: score=" in caplog.text


def test_collect_online_research_sources_fetches_bounded_docs(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    settings = replace(
        parse_settings(valid_settings_dict()),
        research_online_source_urls=[
            "https://docs.langchain.com/oss/python/langgraph/interrupts",
            "https://docs.langchain.com/oss/python/langgraph/checkpointers",
        ],
        research_max_online_source_urls=1,
        research_fetch_timeout_seconds=5,
    )

    class _Headers:
        def get_content_type(self) -> str:
            return "text/html"

        def get_content_charset(self) -> str:
            return "utf-8"

    class _Response:
        def __init__(self, body: str) -> None:
            self.headers = _Headers()
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    seen_urls: list[str] = []

    def fake_urlopen(request, timeout):
        seen_urls.append(request.full_url)
        return _Response(
            "<html><head><title>LangGraph interrupts</title></head>"
            "<body>"
            "<header>Docs nav</header>"
            "<nav>Navigation noise</nav>"
            "<main>"
            "<h1>LangGraph interrupts</h1>"
            "<p>Interrupts pause graph execution and resume with Command.</p>"
            "<aside>Sidebar noise</aside>"
            "<pre><code>Command(resume={\"approved\": True})</code></pre>"
            "</main>"
            "<footer>Footer noise</footer>"
            "</body></html>"
        )

    monkeypatch.setattr("ai_tech_lead.research_sources.urlopen", fake_urlopen)

    sources = collect_online_research_sources("LangGraph interrupt docs", settings)

    assert seen_urls == [
        "https://docs.langchain.com/oss/python/langgraph/interrupts",
    ]
    assert len(sources) == 1
    assert sources[0].title == "LangGraph interrupts"
    assert "Interrupts pause graph execution" in sources[0].summary
    assert "Docs nav" not in sources[0].summary
    assert "Footer noise" not in sources[0].summary
    assert "Command(resume={" in sources[0].summary
    assert "Online research selection: selected 1 source(s)" in caplog.text
    assert "1 online-doc" in caplog.text
    assert "Online research selection request terms" in caplog.text
    assert "Online research candidate URLs (1)" in caplog.text
    assert "Online research candidate: score=" in caplog.text


def test_collect_online_research_sources_handles_markdown_text(monkeypatch) -> None:
    settings = replace(
        parse_settings(valid_settings_dict()),
        research_online_source_urls=[
            "https://docs.langchain.com/oss/python/langgraph/checkpointers",
        ],
        research_max_online_source_urls=1,
        research_fetch_timeout_seconds=5,
    )

    class _Headers:
        def get_content_type(self) -> str:
            return "text/markdown"

        def get_content_charset(self) -> str:
            return "utf-8"

    class _Response:
        def __init__(self, body: str) -> None:
            self.headers = _Headers()
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request, timeout):
        return _Response(
            "# LangGraph checkpointers\n\n"
            "Checkpointers persist graph state.\n\n"
            "- Resume with Command.\n"
        )

    monkeypatch.setattr("ai_tech_lead.research_sources.urlopen", fake_urlopen)

    sources = collect_online_research_sources("LangGraph checkpointers", settings)

    assert len(sources) == 1
    assert sources[0].title == "LangGraph checkpointers"
    assert "Checkpointers persist graph state." in sources[0].summary
    assert "Resume with Command." in sources[0].excerpt


def test_collect_local_research_sources_refreshes_stale_cache_entries(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs"
    research_dir = docs_dir / "research"
    research_dir.mkdir(parents=True)

    note_path = research_dir / "stale-langgraph-note.md"
    note_path.write_text(
        "---\n"
        "topic: Stale LangGraph note\n"
        "date: 2026-01-01\n"
        "sources:\n"
        "  - https://docs.langchain.com/oss/python/langgraph/interrupts\n"
        "---\n\n"
        "## Summary\n"
        "Old summary text.\n",
        encoding="utf-8",
    )
    (research_dir / "INDEX.md").write_text(
        "# Research Cache Index\n\n"
        "## LangGraph / Workflow\n\n"
        "- [stale-langgraph-note.md](stale-langgraph-note.md) — "
        "Stale LangGraph note (2026-01-01)\n",
        encoding="utf-8",
    )

    settings = replace(
        parse_settings(valid_settings_dict()),
        project_root=str(tmp_path),
        research_local_index_paths=["docs/research/INDEX.md"],
        research_online_source_urls=[
            "https://docs.langchain.com/oss/python/langgraph/interrupts",
        ],
        research_max_local_sources=1,
        research_fetch_timeout_seconds=5,
    )

    class _Headers:
        def get_content_type(self) -> str:
            return "text/html"

        def get_content_charset(self) -> str:
            return "utf-8"

    class _Response:
        def __init__(self, body: str) -> None:
            self.headers = _Headers()
            self._body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://docs.langchain.com/oss/python/langgraph/interrupts"
        return _Response(
            "<html><head><title>Refreshed LangGraph note</title></head>"
            "<body><main><p>Fresh summary text.</p></main></body></html>"
        )

    caplog.set_level(logging.INFO)
    monkeypatch.setattr("ai_tech_lead.research_sources.urlopen", fake_urlopen)

    sources = collect_local_research_sources("LangGraph stale note", settings)

    assert len(sources) == 1
    assert sources[0].title == "Refreshed LangGraph note"
    assert "Fresh summary text." in sources[0].summary
    updated_note = note_path.read_text(encoding="utf-8")
    assert "Old summary text." not in updated_note
    assert "Fresh summary text." in updated_note
    assert "Refreshed LangGraph note" in updated_note
    assert "Refreshed stale research cache entry" in caplog.text
