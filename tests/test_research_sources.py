from __future__ import annotations

import logging
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.research_sources import (
    ResearchUrlSafetyError,
    collect_local_research_sources,
    collect_online_research_sources,
    validate_outbound_research_url,
)


class _FakeHeaders:
    def __init__(self, *, content_type: str = "text/html", content_length: str | None = None) -> None:
        self._content_type = content_type
        self._content_length = content_length

    def get_content_type(self) -> str:
        return self._content_type

    def get_content_charset(self) -> str:
        return "utf-8"

    def get(self, key: str, default=None):
        if key == "Content-Length":
            return self._content_length
        return default


class _FakeResponse:
    def __init__(self, body: str, *, content_type: str = "text/html") -> None:
        self.headers = _FakeHeaders(content_type=content_type)
        self._body = body.encode("utf-8")
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._body[self._offset :]
            self._offset = len(self._body)
            return chunk
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FakeOpener:
    def __init__(self, fetch_fn) -> None:
        self._fetch_fn = fetch_fn

    def open(self, request, timeout=None):
        return self._fetch_fn(request, timeout)


def _fake_getaddrinfo_public(_host, _port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _patch_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.research_sources.socket.getaddrinfo", _fake_getaddrinfo_public
    )


def _patch_opener(monkeypatch, fetch_fn) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.research_sources._RESEARCH_FETCH_OPENER", _FakeOpener(fetch_fn)
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


def test_collect_local_research_sources_skips_directory_index_entries(
    tmp_path: Path, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    docs_dir = tmp_path / "docs"
    (docs_dir / "diagrams").mkdir(parents=True)

    doc_path = docs_dir / "ARCHITECTURE.md"
    doc_path.write_text(
        "# Architecture\n\nLangGraph interrupts and checkpointers should stay separate.\n",
        encoding="utf-8",
    )
    (docs_dir / "INDEX.md").write_text(
        "# Documentation Index\n\n"
        "## Core project documents\n\n"
        "- `diagrams/` - generated PNG and hash artifact for the main coding workflow.\n"
        "- `ARCHITECTURE.md` - LangGraph interrupts and checkpointers.\n",
        encoding="utf-8",
    )

    settings = replace(
        parse_settings(valid_settings_dict()),
        project_root=str(tmp_path),
        research_local_index_paths=["docs/INDEX.md"],
        research_max_local_sources=2,
    )

    sources = collect_local_research_sources("LangGraph interrupts and checkpointers", settings)

    assert {source.title for source in sources} == {"ARCHITECTURE.md"}
    assert "Local docs source is a directory, skipping" in caplog.text


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

    seen_urls: list[str] = []

    def fake_fetch(request, timeout):
        seen_urls.append(request.full_url)
        return _FakeResponse(
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

    _patch_public_dns(monkeypatch)
    _patch_opener(monkeypatch, fake_fetch)

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

    def fake_fetch(request, timeout):
        return _FakeResponse(
            "# LangGraph checkpointers\n\n"
            "Checkpointers persist graph state.\n\n"
            "- Resume with Command.\n",
            content_type="text/markdown",
        )

    _patch_public_dns(monkeypatch)
    _patch_opener(monkeypatch, fake_fetch)

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

    def fake_fetch(request, timeout):
        assert request.full_url == "https://docs.langchain.com/oss/python/langgraph/interrupts"
        return _FakeResponse(
            "<html><head><title>Refreshed LangGraph note</title></head>"
            "<body><main><p>Fresh summary text.</p></main></body></html>"
        )

    caplog.set_level(logging.INFO)
    _patch_public_dns(monkeypatch)
    _patch_opener(monkeypatch, fake_fetch)

    sources = collect_local_research_sources("LangGraph stale note", settings)

    assert len(sources) == 1
    assert sources[0].title == "Refreshed LangGraph note"
    assert "Fresh summary text." in sources[0].summary
    updated_note = note_path.read_text(encoding="utf-8")
    assert "Old summary text." not in updated_note
    assert "Fresh summary text." in updated_note
    assert "Refreshed LangGraph note" in updated_note
    assert "Refreshed stale research cache entry" in caplog.text


def test_validate_outbound_research_url_accepts_public_https(monkeypatch) -> None:
    _patch_public_dns(monkeypatch)
    validate_outbound_research_url("https://docs.example.com/page")


def test_validate_outbound_research_url_rejects_bad_scheme() -> None:
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("ftp://docs.example.com/page")


def test_validate_outbound_research_url_rejects_missing_hostname() -> None:
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("https:///no-host")


def test_validate_outbound_research_url_rejects_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.research_sources.socket.getaddrinfo",
        lambda _host, _port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("https://localhost/page")


def test_validate_outbound_research_url_rejects_private_range(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.research_sources.socket.getaddrinfo",
        lambda _host, _port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("https://internal.example.com/page")


def test_validate_outbound_research_url_rejects_cloud_metadata_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.research_sources.socket.getaddrinfo",
        lambda _host, _port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))
        ],
    )
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("https://metadata.example.com/latest")


def test_validate_outbound_research_url_fails_closed_on_mixed_public_and_private(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.research_sources.socket.getaddrinfo",
        lambda _host, _port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
        ],
    )
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("https://mixed.example.com/page")


def test_validate_outbound_research_url_rejects_dns_failure(monkeypatch) -> None:
    def fake_getaddrinfo(_host, _port):
        raise OSError("name resolution failed")

    monkeypatch.setattr("ai_tech_lead.research_sources.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ResearchUrlSafetyError):
        validate_outbound_research_url("https://does-not-resolve.example.com/page")


def test_safe_redirect_handler_blocks_redirect_to_private_ip(monkeypatch) -> None:
    from ai_tech_lead.research_sources import _SafeRedirectHandler

    def fake_getaddrinfo(host, port):
        if host == "evil.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        return _fake_getaddrinfo_public(host, port)

    monkeypatch.setattr("ai_tech_lead.research_sources.socket.getaddrinfo", fake_getaddrinfo)
    handler = _SafeRedirectHandler()

    with pytest.raises(ResearchUrlSafetyError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.com/steal")


def test_collect_online_research_sources_skips_oversized_response(monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING)
    settings = replace(
        parse_settings(valid_settings_dict()),
        research_online_source_urls=[
            "https://docs.langchain.com/oss/python/langgraph/interrupts",
        ],
        research_max_online_source_urls=1,
        research_fetch_timeout_seconds=5,
        research_max_fetch_bytes=10,
    )

    def fake_fetch(request, timeout):
        return _FakeResponse("<html>" + ("x" * 100) + "</html>")

    _patch_public_dns(monkeypatch)
    _patch_opener(monkeypatch, fake_fetch)

    sources = collect_online_research_sources("LangGraph interrupt docs", settings)

    assert sources == []
    assert "Online research source failed" in caplog.text


def test_collect_online_research_sources_skips_response_over_content_length_cap(
    monkeypatch, caplog
) -> None:
    caplog.set_level(logging.WARNING)
    settings = replace(
        parse_settings(valid_settings_dict()),
        research_online_source_urls=[
            "https://docs.langchain.com/oss/python/langgraph/interrupts",
        ],
        research_max_online_source_urls=1,
        research_fetch_timeout_seconds=5,
        research_max_fetch_bytes=10,
    )

    def fake_fetch(request, timeout):
        response = _FakeResponse("<html>" + ("x" * 100) + "</html>")
        response.headers = _FakeHeaders(content_length="1000")
        return response

    _patch_public_dns(monkeypatch)
    _patch_opener(monkeypatch, fake_fetch)

    sources = collect_online_research_sources("LangGraph interrupt docs", settings)

    assert sources == []
    assert "Online research source failed" in caplog.text
