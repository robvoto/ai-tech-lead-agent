from __future__ import annotations

import pytest

from ai_tech_lead.app_settings import parse_settings, settings_to_dict

from helpers import valid_settings_dict


def test_parse_settings_round_trips_valid_config() -> None:
    raw_settings = valid_settings_dict()

    settings = parse_settings(raw_settings)

    assert settings.backlog_path == "docs/BACKLOG.md"
    assert settings.coding_agent_command == "codex"
    assert settings.coding_agent_args == ["--ask-for-approval", "never", "exec"]
    assert settings.execute_coding_agent is False
    assert settings.telegram_enabled is True
    assert settings.telegram_transport == "polling"
    assert settings.telegram_api_base_url == "https://api.telegram.org"
    assert settings.telegram_long_poll_timeout_seconds == 25
    assert settings.telegram_max_fix_request_chars == 3000
    assert settings.telegram_max_message_chars == 3900
    assert settings.telegram_allowed_chat_ids == []
    assert settings.telegram_webhook_url == ""
    assert settings.admin_bind_host == "127.0.0.1"
    assert settings.admin_bind_port == 8766
    assert settings.orchestrator_ai_enabled is False
    assert settings.orchestrator_ai_model == "gpt-4.1-mini"
    assert settings.orchestrator_ai_max_output_tokens == 300
    assert settings.orchestrator_ai_timeout_seconds == 20
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


def test_parse_settings_rejects_prompt_placeholder_drift() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["prompts"]["execution_brief_template"] = "Request:\n{request}"

    with pytest.raises(ValueError, match="missing placeholders"):
        parse_settings(raw_settings)
