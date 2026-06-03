"""Validated settings for the local coding-agent supervisor."""

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
        "constraint_list",
        "relevant_files",
        "request",
        "risk_notes",
    },
    "agent_instruction_template": {
        "allowed_directories",
        "approved",
        "brief",
        "max_runtime_minutes",
        "needs_approval",
        "request",
    },
}


@dataclass(frozen=True)
class CodingAgentSettings:
    """Settings that control coding-agent supervision behaviour."""

    max_runtime_minutes: int
    allowed_directories: list[str]
    watched_directories: list[str]
    risk_terms: list[str]
    brief_constraints: list[str]
    acceptance_criteria: list[str]
    risk_notes: list[str]
    prompts: dict[str, str]


def load_settings(settings_path: Path = SETTINGS_PATH) -> CodingAgentSettings:
    """Load and validate coding-agent settings from JSON."""

    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")

    with settings_path.open(encoding="utf-8") as file:
        raw_settings = json.load(file)

    if not isinstance(raw_settings, dict):
        raise ValueError(f"Settings file must contain a JSON object: {settings_path}")

    return parse_settings(raw_settings)


def save_settings(settings: CodingAgentSettings, settings_path: Path = SETTINGS_PATH) -> None:
    """Write validated coding-agent settings to JSON."""

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(_settings_to_dict(settings), indent=2) + "\n",
        encoding="utf-8",
    )


def parse_settings(raw_settings: dict[str, Any]) -> CodingAgentSettings:
    """Convert raw JSON data into validated settings."""

    settings = CodingAgentSettings(
        max_runtime_minutes=_required_positive_int(raw_settings, "max_runtime_minutes"),
        allowed_directories=_required_string_list(raw_settings, "allowed_directories"),
        watched_directories=_required_string_list(raw_settings, "watched_directories"),
        risk_terms=_required_string_list(raw_settings, "risk_terms"),
        brief_constraints=_required_string_list(raw_settings, "brief_constraints"),
        acceptance_criteria=_required_string_list(raw_settings, "acceptance_criteria"),
        risk_notes=_required_string_list(raw_settings, "risk_notes"),
        prompts=_required_prompt_map(raw_settings),
    )

    _validate_prompt_placeholders(settings.prompts)
    return settings


def _required_positive_int(raw_settings: dict[str, Any], key: str) -> int:
    value = raw_settings.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"Setting '{key}' must be a positive integer.")
    return value


def _required_string_list(raw_settings: dict[str, Any], key: str) -> list[str]:
    value = raw_settings.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Setting '{key}' must be a list of strings.")

    cleaned_values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(cleaned_values) != len(value) or not cleaned_values:
        raise ValueError(f"Setting '{key}' must contain at least one non-empty string.")

    return cleaned_values


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


def _settings_to_dict(settings: CodingAgentSettings) -> dict[str, Any]:
    return {
        "max_runtime_minutes": settings.max_runtime_minutes,
        "allowed_directories": settings.allowed_directories,
        "watched_directories": settings.watched_directories,
        "risk_terms": settings.risk_terms,
        "brief_constraints": settings.brief_constraints,
        "acceptance_criteria": settings.acceptance_criteria,
        "risk_notes": settings.risk_notes,
        "prompts": settings.prompts,
    }
