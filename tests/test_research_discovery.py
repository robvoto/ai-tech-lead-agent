from __future__ import annotations

from dataclasses import replace

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.orchestrator_llm import OrchestratorLlmError, OrchestratorWebSearchResult
from ai_tech_lead.research_discovery import discover_official_source
from ai_tech_lead.research_sources import ResearchUrlSafetyError


def _settings(**overrides):
    fields = {"research_discovery_enabled": True}
    fields.update(overrides)
    return replace(parse_settings(valid_settings_dict()), **fields)


def test_discover_official_source_disabled_makes_no_call(monkeypatch) -> None:
    def fail_if_called(**_kwargs):
        raise AssertionError("web search should not be called when discovery is disabled")

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search", fail_if_called
    )

    result = discover_official_source(
        "What are Telegram Bot API's official retry rules?",
        _settings(research_discovery_enabled=False),
    )

    assert result is None


def test_discover_official_source_call_failure_returns_none(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")

    def fake_call_orchestrator_web_search(**_kwargs):
        raise OrchestratorLlmError("timed out")

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )

    result = discover_official_source(
        "What are Telegram Bot API's official retry rules?", _settings()
    )

    assert result is None
    assert "discovery call failed" in caplog.text.lower()


def test_discover_official_source_finds_citation_candidate(monkeypatch) -> None:
    def fake_call_orchestrator_web_search(**_kwargs):
        return OrchestratorWebSearchResult(
            data={
                "output": [
                    {
                        "content": [
                            {
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://core.telegram.org/bots/api",
                                        "title": "Telegram Bot API",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            text="Found it.",
        )

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.validate_outbound_research_url", lambda _url: None
    )

    result = discover_official_source(
        "What are Telegram Bot API's official retry rules?", _settings()
    )

    assert result is not None
    assert result.url == "https://core.telegram.org/bots/api"
    assert result.title == "Telegram Bot API"


def test_discover_official_source_finds_candidate_in_unfamiliar_shape(monkeypatch) -> None:
    def fake_call_orchestrator_web_search(**_kwargs):
        return OrchestratorWebSearchResult(
            data={"result": {"nested": {"deeply": {"url": "https://example.com/docs"}}}},
            text="",
        )

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.validate_outbound_research_url", lambda _url: None
    )

    result = discover_official_source("some gap", _settings())

    assert result is not None
    assert result.url == "https://example.com/docs"


def test_discover_official_source_falls_back_to_text_url_scan(monkeypatch) -> None:
    def fake_call_orchestrator_web_search(**_kwargs):
        return OrchestratorWebSearchResult(
            data={"output_text": "See https://core.telegram.org/bots/api for details."},
            text="See https://core.telegram.org/bots/api for details.",
        )

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.validate_outbound_research_url", lambda _url: None
    )

    result = discover_official_source("some gap", _settings())

    assert result is not None
    assert result.url == "https://core.telegram.org/bots/api"


def test_discover_official_source_all_candidates_unsafe_declines_gracefully(
    monkeypatch, caplog
) -> None:
    caplog.set_level("INFO")

    def fake_call_orchestrator_web_search(**_kwargs):
        return OrchestratorWebSearchResult(
            data={
                "citations": [
                    {"url": "http://169.254.169.254/latest/meta-data", "title": "bad"},
                ]
            },
            text="",
        )

    def fake_validate(url: str) -> None:
        raise ResearchUrlSafetyError(f"unsafe: {url}")

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.validate_outbound_research_url", fake_validate
    )

    result = discover_official_source("some gap", _settings())

    assert result is None
    assert "found no safe candidate" in caplog.text.lower()


def test_discover_official_source_returns_first_valid_of_mixed_candidates(monkeypatch) -> None:
    def fake_call_orchestrator_web_search(**_kwargs):
        return OrchestratorWebSearchResult(
            data={
                "citations": [
                    {"url": "http://169.254.169.254/latest/meta-data", "title": "unsafe"},
                    {"url": "https://core.telegram.org/bots/api", "title": "safe"},
                ]
            },
            text="",
        )

    def fake_validate(url: str) -> None:
        if "169.254" in url:
            raise ResearchUrlSafetyError("unsafe")

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )
    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.validate_outbound_research_url", fake_validate
    )

    result = discover_official_source("some gap", _settings())

    assert result is not None
    assert result.url == "https://core.telegram.org/bots/api"


def test_discover_official_source_rejects_candidate_outside_trusted_domains(
    monkeypatch, caplog
) -> None:
    caplog.set_level("WARNING")

    def fake_call_orchestrator_web_search(**_kwargs):
        return OrchestratorWebSearchResult(
            data={"citations": [{"url": "https://example.com/blog", "title": "Unofficial"}]},
            text="",
        )

    monkeypatch.setattr(
        "ai_tech_lead.research_discovery.call_orchestrator_web_search",
        fake_call_orchestrator_web_search,
    )

    result = discover_official_source(
        "What is the Python pathlib contract?",
        _settings(),
        trusted_domains=("docs.python.org",),
    )

    assert result is None
    assert "rejected by trusted-domain policy" in caplog.text
