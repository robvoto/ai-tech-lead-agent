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


def test_parse_settings_rejects_prompt_placeholder_drift() -> None:
    raw_settings = valid_settings_dict()
    raw_settings["prompts"]["agent_instruction_template"] = "Task:\n{request}"

    with pytest.raises(ValueError, match="missing placeholders"):
        parse_settings(raw_settings)
