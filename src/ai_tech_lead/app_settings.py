"""Validated local settings for the AI Tech Lead prototype."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_tech_lead.config import PROJECT_ROOT, SETTINGS_PATH

ALLOWED_TELEGRAM_TRANSPORTS = {"polling", "webhook"}


@dataclass(frozen=True)
class AppSettings:
    """Small local settings that are real inputs to the current prototype."""

    project_root: str
    backlog_path: str
    max_runtime_minutes: int
    coding_agent_command: str
    coding_agent_args: list[str]
    execute_coding_agent: bool
    telegram_enabled: bool
    allowed_directories: list[str]
    watched_directories: list[str]
    brief_constraints: list[str]
    acceptance_criteria: list[str]
    risk_notes: list[str]
    telegram_transport: str
    telegram_api_base_url: str
    telegram_long_poll_timeout_seconds: int
    telegram_max_code_request_chars: int
    telegram_max_message_chars: int
    telegram_allowed_chat_ids: list[str]
    telegram_webhook_url: str
    telegram_webhook_bind_host: str
    telegram_webhook_bind_port: int
    telegram_webhook_secret_token: str
    admin_bind_host: str
    admin_bind_port: int
    orchestrator_ai_enabled: bool
    orchestrator_ai_model: str
    orchestrator_ai_max_output_tokens: int
    orchestrator_ai_timeout_seconds: int
    coding_agent_progress_interval_seconds: int


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

    if "prompts" in raw_settings:
        raise ValueError("Setting 'prompts' was removed; use data/prompts.json instead.")

    settings = AppSettings(
        project_root=_optional_string(
            raw_settings,
            "project_root",
            default=str(PROJECT_ROOT),
        ),
        backlog_path=_required_string(raw_settings, "backlog_path"),
        max_runtime_minutes=_required_positive_int(raw_settings, "max_runtime_minutes"),
        coding_agent_command=_required_string(raw_settings, "coding_agent_command"),
        coding_agent_args=_required_string_list(
            raw_settings,
            "coding_agent_args",
            allow_empty=True,
        ),
        execute_coding_agent=_required_bool(raw_settings, "execute_coding_agent"),
        telegram_enabled=_optional_bool(
            raw_settings,
            "telegram_enabled",
            default=False,
        ),
        allowed_directories=_required_string_list(raw_settings, "allowed_directories"),
        watched_directories=_required_string_list(raw_settings, "watched_directories"),
        brief_constraints=_required_string_list(raw_settings, "brief_constraints"),
        acceptance_criteria=_required_string_list(raw_settings, "acceptance_criteria"),
        risk_notes=_required_string_list(raw_settings, "risk_notes"),
        telegram_transport=_required_telegram_transport(raw_settings),
        telegram_api_base_url=_optional_url(
            raw_settings,
            "telegram_api_base_url",
            default="https://api.telegram.org",
        ),
        telegram_long_poll_timeout_seconds=_optional_positive_int(
            raw_settings,
            "telegram_long_poll_timeout_seconds",
            default=25,
        ),
        telegram_max_code_request_chars=_optional_positive_int(
            raw_settings,
            "telegram_max_code_request_chars",
            default=3000,
        ),
        telegram_max_message_chars=_optional_positive_int(
            raw_settings,
            "telegram_max_message_chars",
            default=3900,
        ),
        telegram_allowed_chat_ids=_required_string_list(
            raw_settings,
            "telegram_allowed_chat_ids",
            allow_empty=True,
        ),
        telegram_webhook_url=_optional_string(raw_settings, "telegram_webhook_url"),
        telegram_webhook_bind_host=_optional_string(
            raw_settings,
            "telegram_webhook_bind_host",
            default="127.0.0.1",
        ),
        telegram_webhook_bind_port=_optional_positive_int(
            raw_settings,
            "telegram_webhook_bind_port",
            default=8080,
        ),
        telegram_webhook_secret_token=_optional_string(
            raw_settings,
            "telegram_webhook_secret_token",
        ),
        admin_bind_host=_optional_string(
            raw_settings,
            "admin_bind_host",
            default="127.0.0.1",
        ),
        admin_bind_port=_optional_positive_int(
            raw_settings,
            "admin_bind_port",
            default=8766,
        ),
        orchestrator_ai_enabled=_optional_bool(
            raw_settings,
            "orchestrator_ai_enabled",
            default=False,
        ),
        orchestrator_ai_model=_optional_string(
            raw_settings,
            "orchestrator_ai_model",
            default="gpt-4.1-mini",
        ),
        orchestrator_ai_max_output_tokens=_optional_positive_int(
            raw_settings,
            "orchestrator_ai_max_output_tokens",
            default=300,
        ),
        orchestrator_ai_timeout_seconds=_optional_positive_int(
            raw_settings,
            "orchestrator_ai_timeout_seconds",
            default=20,
        ),
        coding_agent_progress_interval_seconds=_optional_positive_int(
            raw_settings,
            "coding_agent_progress_interval_seconds",
            default=10,
        ),
    )
    _validate_telegram_settings(settings)
    return settings


def settings_to_dict(settings: AppSettings) -> dict[str, Any]:
    """Convert validated settings into JSON-serializable data."""

    return {
        "project_root": settings.project_root,
        "backlog_path": settings.backlog_path,
        "max_runtime_minutes": settings.max_runtime_minutes,
        "coding_agent_command": settings.coding_agent_command,
        "coding_agent_args": settings.coding_agent_args,
        "execute_coding_agent": settings.execute_coding_agent,
        "telegram_enabled": settings.telegram_enabled,
        "allowed_directories": settings.allowed_directories,
        "watched_directories": settings.watched_directories,
        "brief_constraints": settings.brief_constraints,
        "acceptance_criteria": settings.acceptance_criteria,
        "risk_notes": settings.risk_notes,
        "telegram_transport": settings.telegram_transport,
        "telegram_api_base_url": settings.telegram_api_base_url,
        "telegram_long_poll_timeout_seconds": settings.telegram_long_poll_timeout_seconds,
        "telegram_max_code_request_chars": settings.telegram_max_code_request_chars,
        "telegram_max_message_chars": settings.telegram_max_message_chars,
        "telegram_allowed_chat_ids": settings.telegram_allowed_chat_ids,
        "telegram_webhook_url": settings.telegram_webhook_url,
        "telegram_webhook_bind_host": settings.telegram_webhook_bind_host,
        "telegram_webhook_bind_port": settings.telegram_webhook_bind_port,
        "telegram_webhook_secret_token": settings.telegram_webhook_secret_token,
        "admin_bind_host": settings.admin_bind_host,
        "admin_bind_port": settings.admin_bind_port,
        "orchestrator_ai_enabled": settings.orchestrator_ai_enabled,
        "orchestrator_ai_model": settings.orchestrator_ai_model,
        "orchestrator_ai_max_output_tokens": settings.orchestrator_ai_max_output_tokens,
        "orchestrator_ai_timeout_seconds": settings.orchestrator_ai_timeout_seconds,
        "coding_agent_progress_interval_seconds": settings.coding_agent_progress_interval_seconds,
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


def _optional_bool(
    raw_settings: dict[str, Any],
    key: str,
    default: bool = False,
) -> bool:
    value = raw_settings.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Setting '{key}' must be a boolean.")
    return value


def _optional_string(
    raw_settings: dict[str, Any],
    key: str,
    default: str = "",
) -> str:
    value = raw_settings.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"Setting '{key}' must be a string.")
    cleaned_value = value.strip()
    return cleaned_value if cleaned_value else default


def _optional_positive_int(
    raw_settings: dict[str, Any],
    key: str,
    default: int,
) -> int:
    value = raw_settings.get(key, default)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"Setting '{key}' must be a positive integer.")
    return value


def _optional_url(
    raw_settings: dict[str, Any],
    key: str,
    default: str,
) -> str:
    value = raw_settings.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"Setting '{key}' must be a string.")

    cleaned_value = value.strip()
    if not cleaned_value:
        return default
    if not cleaned_value.startswith(("http://", "https://")):
        raise ValueError(f"Setting '{key}' must start with http:// or https://.")
    return cleaned_value


def _required_telegram_transport(raw_settings: dict[str, Any]) -> str:
    value = raw_settings.get("telegram_transport", "polling")
    if not isinstance(value, str):
        raise ValueError("Setting 'telegram_transport' must be a string.")

    normalized_value = value.strip().lower()
    if normalized_value not in ALLOWED_TELEGRAM_TRANSPORTS:
        allowed = ", ".join(sorted(ALLOWED_TELEGRAM_TRANSPORTS))
        raise ValueError(f"Setting 'telegram_transport' must be one of: {allowed}.")
    return normalized_value


def _validate_telegram_settings(settings: AppSettings) -> None:
    if settings.telegram_transport == "webhook":
        if not settings.telegram_webhook_url:
            raise ValueError("Setting 'telegram_webhook_url' is required for webhook mode.")
        if not settings.telegram_webhook_secret_token:
            raise ValueError(
                "Setting 'telegram_webhook_secret_token' is required for webhook mode."
            )
