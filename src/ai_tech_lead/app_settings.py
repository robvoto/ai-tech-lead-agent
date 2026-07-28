"""Validated local settings for the AI Tech Lead prototype."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_tech_lead.config import PROJECT_ROOT, SETTINGS_PATH

ALLOWED_TELEGRAM_TRANSPORTS = {"polling", "webhook"}
PROJECT_REGISTRY_KEY = "project_registry"
ALLOWED_PROJECT_ROOTS_KEY = "allowed_project_roots"
LEGACY_ALLOWED_PROJECT_ROOTS_KEY = "army_allowed_project_roots"


@dataclass(frozen=True)
class ProjectRegistryEntry:
    """One authorised target-project location AI Tech Lead may operate against."""

    root: str
    name: str
    platform: str
    required_credentials_env: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "name": self.name,
            "platform": self.platform,
            "required_credentials_env": list(self.required_credentials_env),
        }


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
    project_context: list[str]
    allowed_directories: list[str]
    watched_directories: list[str]
    brief_constraints: list[str]
    acceptance_criteria: list[str]
    risk_notes: list[str]
    research_local_index_paths: list[str]
    research_min_local_sources: int
    research_max_local_sources: int
    research_allowed_domains: list[str]
    research_online_source_urls: list[str]
    research_max_online_source_urls: int
    research_fetch_timeout_seconds: int
    research_max_excerpt_chars: int
    research_max_fetch_bytes: int
    research_code_context_enabled: bool
    research_max_code_context_files: int
    research_discovery_enabled: bool
    research_discovery_timeout_seconds: int
    research_discovery_max_candidates: int
    knowledge_store_path: str
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
    project_registry: list[ProjectRegistryEntry]
    sleep_mode: bool
    backlog_project_key: str
    backlog_spreadsheet_id: str
    backlog_sheet_name: str
    backlog_google_credentials_path: str
    backlog_projects: dict[str, dict[str, str]]
    backlog_pending_update_max_attempts: int

    def project_registry_entry_for_root(
        self, project_root: str
    ) -> ProjectRegistryEntry | None:
        resolved_root = str(Path(project_root).resolve())
        for entry in self.project_registry:
            if entry.root == resolved_root:
                return entry
        return None


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
        project_context=_required_string_list(raw_settings, "project_context"),
        allowed_directories=_required_string_list(raw_settings, "allowed_directories"),
        watched_directories=_required_string_list(raw_settings, "watched_directories"),
        brief_constraints=_required_string_list(raw_settings, "brief_constraints"),
        acceptance_criteria=_required_string_list(raw_settings, "acceptance_criteria"),
        risk_notes=_required_string_list(raw_settings, "risk_notes"),
        research_local_index_paths=_required_string_list(
            raw_settings,
            "research_local_index_paths",
        ),
        research_min_local_sources=_required_positive_int(
            raw_settings,
            "research_min_local_sources",
        ),
        research_max_local_sources=_required_positive_int(
            raw_settings,
            "research_max_local_sources",
        ),
        research_allowed_domains=_required_string_list(
            raw_settings,
            "research_allowed_domains",
        ),
        research_online_source_urls=_required_string_list(
            raw_settings,
            "research_online_source_urls",
        ),
        research_max_online_source_urls=_required_positive_int(
            raw_settings,
            "research_max_online_source_urls",
        ),
        research_fetch_timeout_seconds=_required_positive_int(
            raw_settings,
            "research_fetch_timeout_seconds",
        ),
        research_max_excerpt_chars=_required_positive_int(
            raw_settings,
            "research_max_excerpt_chars",
        ),
        research_max_fetch_bytes=_optional_positive_int(
            raw_settings,
            "research_max_fetch_bytes",
            default=2_000_000,
        ),
        research_code_context_enabled=_optional_bool(
            raw_settings,
            "research_code_context_enabled",
            default=True,
        ),
        research_max_code_context_files=_optional_positive_int(
            raw_settings,
            "research_max_code_context_files",
            default=5,
        ),
        research_discovery_enabled=_optional_bool(
            raw_settings,
            "research_discovery_enabled",
            default=False,
        ),
        research_discovery_timeout_seconds=_optional_positive_int(
            raw_settings,
            "research_discovery_timeout_seconds",
            default=20,
        ),
        research_discovery_max_candidates=_optional_positive_int(
            raw_settings,
            "research_discovery_max_candidates",
            default=3,
        ),
        knowledge_store_path=_optional_string(
            raw_settings,
            "knowledge_store_path",
            default="data/knowledge_store.sqlite3",
        ),
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
        project_registry=_optional_project_registry(raw_settings),
        sleep_mode=_optional_bool(
            raw_settings,
            "sleep_mode",
            default=False,
        ),
        backlog_project_key=_required_string(raw_settings, "backlog_project_key"),
        backlog_spreadsheet_id=_required_string(raw_settings, "backlog_spreadsheet_id"),
        backlog_sheet_name=_required_string(raw_settings, "backlog_sheet_name"),
        backlog_google_credentials_path=_required_string(
            raw_settings,
            "backlog_google_credentials_path",
        ),
        backlog_projects=_optional_backlog_projects(raw_settings, "backlog_projects"),
        backlog_pending_update_max_attempts=_optional_positive_int(
            raw_settings,
            "backlog_pending_update_max_attempts",
            default=5,
        ),
    )
    _validate_telegram_settings(settings)
    _validate_research_settings(settings)
    _validate_project_registry_settings(settings)
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
        "project_context": settings.project_context,
        "allowed_directories": settings.allowed_directories,
        "watched_directories": settings.watched_directories,
        "brief_constraints": settings.brief_constraints,
        "acceptance_criteria": settings.acceptance_criteria,
        "risk_notes": settings.risk_notes,
        "research_local_index_paths": settings.research_local_index_paths,
        "research_min_local_sources": settings.research_min_local_sources,
        "research_max_local_sources": settings.research_max_local_sources,
        "research_allowed_domains": settings.research_allowed_domains,
        "research_online_source_urls": settings.research_online_source_urls,
        "research_max_online_source_urls": settings.research_max_online_source_urls,
        "research_fetch_timeout_seconds": settings.research_fetch_timeout_seconds,
        "research_max_excerpt_chars": settings.research_max_excerpt_chars,
        "research_max_fetch_bytes": settings.research_max_fetch_bytes,
        "research_code_context_enabled": settings.research_code_context_enabled,
        "research_max_code_context_files": settings.research_max_code_context_files,
        "research_discovery_enabled": settings.research_discovery_enabled,
        "research_discovery_timeout_seconds": settings.research_discovery_timeout_seconds,
        "research_discovery_max_candidates": settings.research_discovery_max_candidates,
        "knowledge_store_path": settings.knowledge_store_path,
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
        PROJECT_REGISTRY_KEY: [entry.to_dict() for entry in settings.project_registry],
        "sleep_mode": settings.sleep_mode,
        "backlog_project_key": settings.backlog_project_key,
        "backlog_spreadsheet_id": settings.backlog_spreadsheet_id,
        "backlog_sheet_name": settings.backlog_sheet_name,
        "backlog_google_credentials_path": settings.backlog_google_credentials_path,
        "backlog_projects": settings.backlog_projects,
        "backlog_pending_update_max_attempts": settings.backlog_pending_update_max_attempts,
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


def _optional_string_list(
    raw_settings: dict[str, Any],
    key: str,
) -> list[str]:
    value = raw_settings.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Setting '{key}' must be a list of strings.")
    cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if len(cleaned) != len(value):
        raise ValueError(f"Setting '{key}' must contain non-empty strings.")
    return cleaned


def _optional_string_list_with_legacy(
    raw_settings: dict[str, Any],
    key: str,
    *,
    legacy_key: str,
) -> list[str]:
    value = raw_settings.get(key)
    legacy_value = raw_settings.get(legacy_key)

    if value is not None and legacy_value is not None and value != legacy_value:
        raise ValueError(
            f"Settings '{key}' and '{legacy_key}' cannot both be set with different values."
        )

    if value is not None:
        return _optional_string_list(raw_settings, key)
    if legacy_value is not None:
        return _optional_string_list(raw_settings, legacy_key)
    return []


def _optional_project_registry(
    raw_settings: dict[str, Any],
) -> list[ProjectRegistryEntry]:
    value = raw_settings.get(PROJECT_REGISTRY_KEY)
    legacy_present = (
        raw_settings.get(ALLOWED_PROJECT_ROOTS_KEY) is not None
        or raw_settings.get(LEGACY_ALLOWED_PROJECT_ROOTS_KEY) is not None
    )

    if value is not None and legacy_present:
        raise ValueError(
            f"Setting '{PROJECT_REGISTRY_KEY}' cannot be combined with legacy "
            f"'{ALLOWED_PROJECT_ROOTS_KEY}' settings."
        )

    if value is None:
        legacy_roots = _optional_string_list_with_legacy(
            raw_settings,
            ALLOWED_PROJECT_ROOTS_KEY,
            legacy_key=LEGACY_ALLOWED_PROJECT_ROOTS_KEY,
        )
        return [_legacy_project_registry_entry(root) for root in legacy_roots]

    if not isinstance(value, list):
        raise ValueError(f"Setting '{PROJECT_REGISTRY_KEY}' must be a list of project entries.")

    entries: list[ProjectRegistryEntry] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(
                f"Setting '{PROJECT_REGISTRY_KEY}[{index}]' must be an object."
            )
        raw_root = item.get("root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ValueError(
                f"Setting '{PROJECT_REGISTRY_KEY}[{index}].root' must be a non-empty string."
            )
        resolved_root = str(Path(raw_root.strip()).expanduser().resolve())
        raw_name = item.get("name")
        name = str(raw_name).strip() if raw_name is not None else Path(resolved_root).name
        if not name:
            name = Path(resolved_root).name or resolved_root
        raw_platform = item.get("platform", "filesystem")
        if not isinstance(raw_platform, str) or not raw_platform.strip():
            raise ValueError(
                f"Setting '{PROJECT_REGISTRY_KEY}[{index}].platform' must be a non-empty string."
            )
        required_credentials_env = _optional_string_list(item, "required_credentials_env")
        entries.append(
            ProjectRegistryEntry(
                root=resolved_root,
                name=name,
                platform=raw_platform.strip(),
                required_credentials_env=required_credentials_env,
            )
        )
    return entries


def _legacy_project_registry_entry(root: str) -> ProjectRegistryEntry:
    resolved_root = str(Path(root).expanduser().resolve())
    return ProjectRegistryEntry(
        root=resolved_root,
        name=Path(resolved_root).name or resolved_root,
        platform="filesystem",
        required_credentials_env=[],
    )


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


def _optional_backlog_projects(
    raw_settings: dict[str, Any],
    key: str,
) -> dict[str, dict[str, str]]:
    value = raw_settings.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Setting '{key}' must be an object of project_key -> entry.")

    projects: dict[str, dict[str, str]] = {}
    for project_key, entry in value.items():
        if not isinstance(project_key, str) or not project_key.strip():
            raise ValueError(f"Setting '{key}' keys must be non-empty project keys.")
        if not isinstance(entry, dict):
            raise ValueError(f"Setting '{key}[\"{project_key}\"]' must be an object.")
        spreadsheet_id = entry.get("spreadsheet_id")
        sheet_name = entry.get("sheet_name")
        if (
            not isinstance(spreadsheet_id, str)
            or not spreadsheet_id.strip()
            or not isinstance(sheet_name, str)
            or not sheet_name.strip()
        ):
            raise ValueError(
                f"Setting '{key}[\"{project_key}\"]' must define non-empty "
                "spreadsheet_id and sheet_name."
            )
        projects[project_key.strip()] = {
            "spreadsheet_id": spreadsheet_id.strip(),
            "sheet_name": sheet_name.strip(),
        }
    return projects


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


def _validate_research_settings(settings: AppSettings) -> None:
    if settings.research_min_local_sources > settings.research_max_local_sources:
        raise ValueError(
            "Setting 'research_min_local_sources' must be less than or equal to "
            "'research_max_local_sources'."
        )

    if not settings.research_local_index_paths:
        raise ValueError("Setting 'research_local_index_paths' must not be empty.")

    if not settings.research_online_source_urls:
        raise ValueError("Setting 'research_online_source_urls' must not be empty.")

    if settings.research_max_online_source_urls <= 0:
        raise ValueError("Setting 'research_max_online_source_urls' must be positive.")

    if not settings.research_allowed_domains:
        raise ValueError("Setting 'research_allowed_domains' must not be empty.")

    allowed_domains = {domain.lower() for domain in settings.research_allowed_domains}
    for url in settings.research_online_source_urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Each entry in 'research_online_source_urls' must be an http(s) URL.")

        hostname = (parsed.hostname or "").lower()
        if hostname not in allowed_domains and not any(
            hostname.endswith(f".{domain}") for domain in allowed_domains
        ):
            raise ValueError(
                "Each entry in 'research_online_source_urls' must use one of the "
                "allowed research domains."
            )


def _validate_project_registry_settings(settings: AppSettings) -> None:
    if not settings.project_registry:
        raise ValueError(f"Setting '{PROJECT_REGISTRY_KEY}' must include the project root.")

    project_root = str(Path(settings.project_root).resolve())
    registry_roots: set[str] = set()
    for entry in settings.project_registry:
        if entry.root in registry_roots:
            raise ValueError(
                f"Setting '{PROJECT_REGISTRY_KEY}' cannot contain duplicate roots "
                f"({entry.root})."
            )
        registry_roots.add(entry.root)

    if project_root not in registry_roots:
        raise ValueError(
            f"Setting '{PROJECT_REGISTRY_KEY}' must include the current project_root "
            f"({project_root})."
        )
