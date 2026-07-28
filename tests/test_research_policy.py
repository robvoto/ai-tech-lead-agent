from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings
from ai_tech_lead.research_policy import is_url_on_trusted_domain, select_research_policy


def _settings(**overrides):
    return replace(parse_settings(valid_settings_dict()), **overrides)


def test_python_request_selects_python_official_docs(tmp_path: Path) -> None:
    decision = select_research_policy(
        "What is the supported Python pathlib behaviour?",
        _settings(project_root=str(tmp_path)),
        project_root=tmp_path,
    )

    assert "python" in decision.profile_names
    assert "docs.python.org" in decision.trusted_domains
    assert decision.seed_urls[0] == "https://docs.python.org/3/"


def test_litellm_request_selects_litellm_before_repository_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["litellm"]\n', encoding="utf-8"
    )

    decision = select_research_policy(
        "How should LiteLLM retries be configured?",
        _settings(project_root=str(tmp_path)),
        project_root=tmp_path,
    )

    assert decision.profile_names[0] == "litellm"
    assert "docs.litellm.ai" in decision.trusted_domains
    assert decision.seed_urls[0] == "https://docs.litellm.ai/docs/"


def test_telegram_request_selects_telegram_official_api(tmp_path: Path) -> None:
    decision = select_research_policy(
        "What are Telegram Bot API retry rules?",
        _settings(project_root=str(tmp_path)),
        project_root=tmp_path,
    )

    assert "telegram" in decision.profile_names
    assert "core.telegram.org" in decision.trusted_domains
    assert decision.seed_urls[0] == "https://core.telegram.org/bots/api"


def test_repository_detection_reads_only_known_bounded_signal_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Uses LiteLLM for model routing.", encoding="utf-8")
    (tmp_path / "unrelated-secret.txt").write_text("telegram", encoding="utf-8")

    decision = select_research_policy(
        "How should retries work?",
        _settings(project_root=str(tmp_path)),
        project_root=tmp_path,
    )

    assert "litellm" in decision.profile_names
    assert "telegram" not in decision.profile_names


def test_trusted_domain_check_accepts_subdomains_and_rejects_lookalikes() -> None:
    domains = ("docs.python.org",)

    assert is_url_on_trusted_domain("https://docs.python.org/3/library/pathlib.html", domains)
    assert not is_url_on_trusted_domain("https://docs.python.org.evil.example/page", domains)
    assert not is_url_on_trusted_domain("https://example.com/docs/python", domains)
