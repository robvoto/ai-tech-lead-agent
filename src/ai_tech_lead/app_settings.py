"""Validated local settings for the AI Tech Lead prototype."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from string import Formatter
from typing import Any

from ai_tech_lead.config import SETTINGS_PATH


REQUIRED_PROMPTS = {
    "execution_brief_template": {
        "acceptance_criteria",
        "approval_reason",
        "constraint_list",
        "relevant_files",
        "request",
        "risk_notes",
    },
    "agent_instruction_template": {
        "allowed_directories",
        "approval_reason",
        "approved",
        "brief",
        "max_runtime_minutes",
        "needs_approval",
        "request",
    },
}


@dataclass(frozen=True)
class AppSettings:
    """Small local settings that are real inputs to the current prototype."""

    backlog_path: str
    max_runtime_minutes: int
    coding_agent_command: str
    coding_agent_args: list[str]
    execute_coding_agent: bool
    allowed_directories: list[str]
    watched_directories: list[str]
    brief_constraints: list[str]
    acceptance_criteria: list[str]
    risk_notes: list[str]
    prompts: dict[str, str]


def load_settings(settings_path: Path = SETTINGS_PATH) -> AppSettings:
    """Load and validate local app settings from JSON."""

    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    with settings_path.open(encoding="utf-8") as file:
        raw_settings = json.load(file)

    if not isinstance(raw_settings, dict):
        raise ValueError(f"Settings file must contain a JSON object: {settings_path}")

    return parse_settings(raw_settings)


def save_settings(settings: AppSettings, settings_path: Path = SETTINGS_PATH) -> None:
    """Write validated local app settings to JSON."""

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings_to_dict(settings), indent=2) + "\n",
        encoding="utf-8",
    )


def parse_settings(raw_settings: dict[str, Any]) -> AppSettings:
    """Convert raw JSON data into validated settings."""

    settings = AppSettings(
        backlog_path=_required_string(raw_settings, "backlog_path"),
        max_runtime_minutes=_required_positive_int(raw_settings, "max_runtime_minutes"),
        coding_agent_command=_required_string(raw_settings, "coding_agent_command"),
        coding_agent_args=_required_string_list(
            raw_settings,
            "coding_agent_args",
            allow_empty=True,
        ),
        execute_coding_agent=_required_bool(raw_settings, "execute_coding_agent"),
        allowed_directories=_required_string_list(raw_settings, "allowed_directories"),
        watched_directories=_required_string_list(raw_settings, "watched_directories"),
        brief_constraints=_required_string_list(raw_settings, "brief_constraints"),
        acceptance_criteria=_required_string_list(raw_settings, "acceptance_criteria"),
        risk_notes=_required_string_list(raw_settings, "risk_notes"),
        prompts=_required_prompt_map(raw_settings),
    )
    _validate_prompt_placeholders(settings.prompts)
    return settings


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    """Convert validated settings into JSON-serializable data."""

    return {
        "backlog_path": settings.backlog_path,
        "max_runtime_minutes": settings.max_runtime_minutes,
        "coding_agent_command": settings.coding_agent_command,
        "coding_agent_args": settings.coding_agent_args,
        "execute_coding_agent": settings.execute_coding_agent,
        "allowed_directories": settings.allowed_directories,
        "watched_directories": settings.watched_directories,
        "brief_constraints": settings.brief_constraints,
        "acceptance_criteria": settings.acceptance_criteria,
        "risk_notes": settings.risk_notes,
        "prompts": settings.prompts,
    }


def _required_positive_int(raw_settings: dict[str, Any], key: str) -> int:
    value = raw_settings.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"Setting '{key}' must be a positive integer.")
    return value


def _required_string(raw_settings: dict[str, Any], key: str) -> str:
    value = raw_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Setting '{key}' must be a non-empty string.")
    return value.strip()


def _required_string_list(
    raw_settings: dict[str, Any],
    key: str,
    allow_empty: bool = False,
) -> list[str]:
    value = raw_settings.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Setting '{key}' must be a list of strings.")

    cleaned_values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(cleaned_values) != len(value) or (not allow_empty and not cleaned_values):
        raise ValueError(f"Setting '{key}' must contain non-empty strings.")

    return cleaned_values


def _required_bool(raw_settings: dict[str, Any], key: str) -> bool:
    value = raw_settings.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Setting '{key}' must be a boolean.")
    return value


def _required_prompt_map(raw_settings: dict[str, Any]) -> dict[str, str]:
    value = raw_settings.get("prompts")
    if not isinstance(value, dict):
        raise ValueError("Setting 'prompts' must be an object.")

    prompts: dict[str, str] = {}
    for prompt_name in REQUIRED_PROMPTS:
        prompt = value.get(prompt_name)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Prompt '{prompt_name}' must be a non-empty string.")
        prompts[prompt_name] = prompt.strip()

    return prompts


def _validate_prompt_placeholders(prompts: dict[str, str]) -> None:
    for prompt_name, required_placeholders in REQUIRED_PROMPTS.items():
        found_placeholders = {
            field_name
            for _, field_name, _, _ in Formatter().parse(prompts[prompt_name])
            if field_name
        }
        missing_placeholders = required_placeholders - found_placeholders
        if missing_placeholders:
            missing = ", ".join(sorted(missing_placeholders))
            raise ValueError(f"Prompt '{prompt_name}' is missing placeholders: {missing}")

        unexpected_placeholders = found_placeholders - required_placeholders
        if unexpected_placeholders:
            unexpected = ", ".join(sorted(unexpected_placeholders))
            raise ValueError(
                f"Prompt '{prompt_name}' has unknown placeholders: {unexpected}"
            )
