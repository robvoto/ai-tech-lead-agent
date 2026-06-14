"""Load and enumerate prompt registry data from `data/prompts.json`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ai_tech_lead.config import DATA_DIR

PROMPT_REGISTRY_PATH = DATA_DIR / "prompts.json"

BACKLOG_OWNERSHIP_RULES_PROMPT_KEY = "backlog_ownership_rules"
CLARIFICATION_CHECK_PROMPT_KEY = "clarification_check"
COMPLETION_SUMMARY_PROMPT_KEY = "completion_summary"
PLAN_REQUEST_INSTRUCTION_PROMPT_KEY = "plan_request_instruction"
PLAN_REVIEW_PROMPT_KEY = "plan_review"
RISK_REVIEW_PROMPT_KEY = "risk_review"
RESEARCH_COMPLEXITY_PROMPT_KEY = "research_complexity"
TASK_FORMULATION_PROMPT_KEY = "task_formulation"
TELEGRAM_AGENT_SYSTEM_PROMPT_KEY = "telegram_agent_system"
RISK_REVIEW_REASON_PROMPT_KEY = "risk_review_reason"
EXECUTION_BRIEF_PROMPT_KEY = "execution_brief"


@dataclass(frozen=True)
class PromptEntry:
    key: str
    prompt_class: str
    purpose: str
    template: str
    used_by: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "prompt_class": self.prompt_class,
            "purpose": self.purpose,
            "used_by": list(self.used_by),
            "template": self.template,
        }


def load_prompt(prompt_key: str, *, path: Path | None = None) -> str:
    """Load one prompt template from the central prompt registry."""

    if path is None:
        path = PROMPT_REGISTRY_PATH

    normalized_prompt_key = prompt_key.strip()
    if not normalized_prompt_key:
        raise ValueError("Prompt key cannot be empty.")

    return _prompt_entry_by_key(normalized_prompt_key, path=path).template


def render_prompt(
    prompt_key: str,
    *,
    path: Path | None = None,
    **replacements: Any,
) -> str:
    """Load a prompt and replace its named placeholders."""

    prompt = load_prompt(prompt_key, path=path)
    for placeholder, value in replacements.items():
        prompt = prompt.replace(f"{{{placeholder}}}", str(value))
    return prompt


def list_prompts(*, path: Path | None = None) -> list[dict[str, Any]]:
    """Return the prompt registry as JSON-friendly data."""

    if path is None:
        path = PROMPT_REGISTRY_PATH

    return [entry.as_dict() for entry in load_prompt_registry(path=path)]


def load_prompt_registry(*, path: Path | None = None) -> tuple[PromptEntry, ...]:
    """Load and validate the full prompt registry from disk."""

    if path is None:
        path = PROMPT_REGISTRY_PATH

    registry_path = Path(path)
    if not registry_path.is_file():
        raise FileNotFoundError(f"Prompt registry not found: {registry_path}")

    return _load_prompt_registry_cached(str(registry_path))


def save_prompt_registry(
    raw_payload: dict[str, Any],
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Validate and persist the central prompt registry."""

    if path is None:
        path = PROMPT_REGISTRY_PATH

    registry_path = Path(path)
    registry = _parse_prompt_registry_payload(raw_payload)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"prompts": [entry.as_dict() for entry in registry]}, indent=2) + "\n",
        encoding="utf-8",
    )
    clear_prompt_registry_cache()
    return [entry.as_dict() for entry in registry]


def clear_prompt_registry_cache() -> None:
    """Invalidate the prompt registry cache after a local edit."""

    _load_prompt_registry_cached.cache_clear()


@lru_cache(maxsize=4)
def _load_prompt_registry_cached(path_text: str) -> tuple[PromptEntry, ...]:
    registry_path = Path(path_text)
    raw_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    return _parse_prompt_registry_payload(raw_payload)


def _prompt_entry_by_key(prompt_key: str, *, path: Path) -> PromptEntry:
    for entry in load_prompt_registry(path=path):
        if entry.key == prompt_key:
            return entry

    raise FileNotFoundError(
        f"Prompt '{prompt_key}' was not found in the prompt registry: {path}"
    )


def _parse_prompt_registry_payload(raw_payload: Any) -> tuple[PromptEntry, ...]:
    if not isinstance(raw_payload, dict):
        raise ValueError("Prompt registry must be a JSON object.")

    raw_prompts = raw_payload.get("prompts")
    if not isinstance(raw_prompts, list) or not raw_prompts:
        raise ValueError("Prompt registry field 'prompts' must be a non-empty list.")

    prompt_entries: list[PromptEntry] = []
    seen_keys: set[str] = set()
    for raw_prompt in raw_prompts:
        entry = _parse_prompt_entry(raw_prompt)
        if entry.key in seen_keys:
            raise ValueError(f"Duplicate prompt key: {entry.key}")
        seen_keys.add(entry.key)
        prompt_entries.append(entry)

    return tuple(prompt_entries)


def _parse_prompt_entry(raw_prompt: Any) -> PromptEntry:
    if not isinstance(raw_prompt, dict):
        raise ValueError("Each prompt entry must be a JSON object.")

    key = _required_string(raw_prompt, "key")
    prompt_class = _required_string(raw_prompt, "prompt_class")
    purpose = _required_string(raw_prompt, "purpose")
    template = _required_string(raw_prompt, "template")
    used_by = _optional_string_list(raw_prompt, "used_by")

    return PromptEntry(
        key=key,
        prompt_class=prompt_class,
        purpose=purpose,
        template=template,
        used_by=tuple(used_by),
    )


def _required_string(raw_payload: dict[str, Any], key: str) -> str:
    value = raw_payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Prompt field '{key}' must be a non-empty string.")
    return value.strip()


def _optional_string_list(raw_payload: dict[str, Any], key: str) -> list[str]:
    value = raw_payload.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Prompt field '{key}' must be a list of strings.")

    cleaned_values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Prompt field '{key}' must contain only non-empty strings.")
        cleaned_values.append(item.strip())
    return cleaned_values
