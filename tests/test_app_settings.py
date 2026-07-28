from __future__ import annotations

import pytest
from helpers import valid_settings_dict

from ai_tech_lead.app_settings import parse_settings, settings_to_dict
from ai_tech_lead.config import PROJECT_ROOT


def test_parse_settings_round_trips_valid_config() -> None:
    raw_settings = valid_settings_dict()

    settings = parse_settings(raw_settings)

    assert settings.project_root == str(PROJECT_ROOT)
    assert settings.backlog_path == raw_settings["backlog_path"]
    assert settings.coding_agent_command == "codex"
    assert settings.coding_agent_args == ["--ask-for-approval", "never", "exec"]
    assert settings.execute_coding_agent is False
    assert settings.telegram_enabled is True
    assert settings.telegram_transport == "polling"
    assert settings.telegram_api_base_url == "https://api.telegram.org"
    assert settings.telegram_long_poll_timeout_seconds == 25
    assert settings.telegram_max_code_request_chars == 3000
    assert settings.telegram_max_message_chars == 3900
    assert settings.telegram_allowed_chat_ids == []
    assert settings.telegram_webhook_url == ""
    assert settings.admin_bind_host == "127.0.0.1"
    assert settings.admin_bind_port == 8766
    assert settings.orchestrator_ai_enabled is False
    assert settings.orchestrator_ai_model == "gpt-4.1-mini"
    assert settings.orchestrator_ai_max_output_tokens == 300
    assert settings.orchestrator_ai_timeout_seconds == 20
    assert settings.research_local_index_paths == ["docs/INDEX.md", "docs/research/INDEX.md"]
    assert settings.research_min_local_sources == 2
    assert settings.research_max_local_sources == 4
    assert settings.research_allowed_domains == ["docs.langchain.com"]
    assert (
        settings.research_online_source_urls[0]
        == "https://docs.langchain.com/oss/python/langgraph/overview"
    )
    assert settings.research_max_online_source_urls == 4
    assert settings.research_fetch_timeout_seconds == 10
    assert settings.research_max_excerpt_chars == 800
    assert settings.knowledge_store_path == "data/knowledge_store.sqlite3"
    assert len(settings.project_registry) == 1
    assert settings.project_registry[0].root == str(PROJECT_ROOT)
    assert settings.project_registry[0].platform == "filesystem"
    assert settings_to_dict(settings) == raw_settings


def test_parse_settings_rejects_invalid_coding_agent_fields() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["coding_agent_command"] = " "

    with pytest.raises(ValueError, match="coding_agent_command"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["coding_agent_args"] = ["exec", 123]

    with pytest.raises(ValueError, match="coding_agent_args"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["execute_coding_agent"] = "false"

    with pytest.raises(ValueError, match="execute_coding_agent"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["telegram_enabled"] = "true"

    with pytest.raises(ValueError, match="telegram_enabled"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["telegram_transport"] = "invalid"

    with pytest.raises(ValueError, match="telegram_transport"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["telegram_transport"] = "webhook"
    raw_settings["telegram_webhook_url"] = ""

    with pytest.raises(ValueError, match="telegram_webhook_url"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["telegram_api_base_url"] = "ftp://example.com"

    with pytest.raises(ValueError, match="telegram_api_base_url"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["research_min_local_sources"] = 5
    raw_settings["research_max_local_sources"] = 4

    with pytest.raises(ValueError, match="research_min_local_sources"):
        parse_settings(raw_settings)

    raw_settings = valid_settings_dict()
    raw_settings["research_online_source_urls"] = ["https://example.com/"]

    with pytest.raises(ValueError, match="allowed research domains"):
        parse_settings(raw_settings)


def test_parse_settings_defaults_project_root_when_missing() -> None:
    raw_settings = valid_settings_dict()
    raw_settings.pop("project_root")

    settings = parse_settings(raw_settings)

    assert settings.project_root.endswith("ai-tech-lead")


def test_parse_settings_rejects_missing_project_context() -> None:
    raw_settings = valid_settings_dict()
    raw_settings.pop("project_context")

    try:
        parse_settings(raw_settings)
        assert False, "Expected ValueError"
    except ValueError as err:
        assert "project_context" in str(err)


def test_parse_settings_rejects_project_registry_without_current_project_root() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["project_registry"] = [
        {
            "root": "/some/other/path",
            "name": "Other Project",
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]

    with pytest.raises(ValueError, match="project_registry"):
        parse_settings(raw_settings)


def test_parse_settings_accepts_legacy_project_root_allowlist() -> None:
    raw_settings = valid_settings_dict()
    raw_settings.pop("project_registry")
    raw_settings["allowed_project_roots"] = [str(PROJECT_ROOT)]

    settings = parse_settings(raw_settings)

    assert [entry.root for entry in settings.project_registry] == [str(PROJECT_ROOT)]
    assert settings_to_dict(settings)["project_registry"] == [
        {
            "root": str(PROJECT_ROOT),
            "name": PROJECT_ROOT.name,
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]


def test_parse_settings_accepts_legacy_army_project_root_allowlist() -> None:
    raw_settings = valid_settings_dict()
    raw_settings.pop("project_registry")
    raw_settings["army_allowed_project_roots"] = [str(PROJECT_ROOT)]

    settings = parse_settings(raw_settings)

    assert [entry.root for entry in settings.project_registry] == [str(PROJECT_ROOT)]
    assert settings_to_dict(settings)["project_registry"] == [
        {
            "root": str(PROJECT_ROOT),
            "name": PROJECT_ROOT.name,
            "platform": "filesystem",
            "required_credentials_env": [],
        }
    ]


def test_parse_settings_rejects_conflicting_new_and_legacy_allowlists() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["allowed_project_roots"] = ["/some/other/path"]

    with pytest.raises(ValueError, match="cannot be combined"):
        parse_settings(raw_settings)


def test_parse_settings_rejects_legacy_allowlist_without_current_project_root() -> None:
    raw_settings = valid_settings_dict()
    raw_settings.pop("project_registry")
    raw_settings["army_allowed_project_roots"] = ["/some/other/path"]

    with pytest.raises(ValueError, match="must include the current project_root"):
        parse_settings(raw_settings)


def test_parse_settings_rejects_duplicate_project_registry_roots() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["project_registry"] = [
        {
            "root": str(PROJECT_ROOT),
            "name": "AI Tech Lead",
            "platform": "filesystem",
            "required_credentials_env": [],
        },
        {
            "root": str(PROJECT_ROOT),
            "name": "Duplicate",
            "platform": "filesystem",
            "required_credentials_env": [],
        },
    ]

    with pytest.raises(ValueError, match="duplicate roots"):
        parse_settings(raw_settings)


def test_parse_settings_rejects_empty_project_context() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["project_context"] = []

    try:
        parse_settings(raw_settings)
        assert False, "Expected ValueError"
    except ValueError as err:
        assert "project_context" in str(err)
