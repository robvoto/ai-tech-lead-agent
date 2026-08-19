"""Single authoritative registry of supported LLM models and their pricing.

Loads config/model_registry.json once and exposes typed lookups so no other
module hardcodes model capability flags or per-token prices. Replaces the
former dual sources (a hardcoded dict in orchestrator_llm.py and
config/llm_pricing.json) that could silently drift apart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.logging_setup import LOGGER_NAME

MODEL_REGISTRY_PATH = PROJECT_ROOT / "config" / "model_registry.json"

logger = logging.getLogger(LOGGER_NAME)


class ModelRegistryError(RuntimeError):
    """Raised when a model is unknown or the registry file is invalid."""


@dataclass(frozen=True)
class ModelCapabilities:
    structured_output: bool
    tool_use: bool
    web_search: bool


@dataclass(frozen=True)
class ModelReasoningConfig:
    supported_efforts: tuple[str, ...]
    default_effort: str
    requires_explicit_effort: bool
    min_recommended_output_tokens: int


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    tier: str
    endpoints: tuple[str, ...]
    capabilities: ModelCapabilities
    reasoning: ModelReasoningConfig | None
    pricing: ModelPricing
    pricing_source: str
    pricing_checked: str


@dataclass(frozen=True)
class CostEstimate:
    usd: float
    is_estimated: bool


_registry_cache: dict[str, ModelSpec] | None = None


def _load_registry(path: Path = MODEL_REGISTRY_PATH) -> dict[str, ModelSpec]:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelRegistryError(f"Could not load model registry from {path}: {error}") from error

    pricing_source = raw.get("pricing_source", "unknown")
    models: dict[str, ModelSpec] = {}
    for model_id, entry in raw.get("models", {}).items():
        reasoning_raw = entry.get("reasoning")
        reasoning = None
        if reasoning_raw is not None:
            reasoning = ModelReasoningConfig(
                supported_efforts=tuple(reasoning_raw["supported_efforts"]),
                default_effort=reasoning_raw["default_effort"],
                requires_explicit_effort=bool(reasoning_raw["requires_explicit_effort"]),
                min_recommended_output_tokens=int(
                    reasoning_raw.get("min_recommended_output_tokens", 0)
                ),
            )
        pricing_raw = entry["pricing"]
        models[model_id] = ModelSpec(
            model_id=model_id,
            tier=entry.get("tier", "unknown"),
            endpoints=tuple(entry.get("endpoints", [])),
            capabilities=ModelCapabilities(**entry.get("capabilities", {})),
            reasoning=reasoning,
            pricing=ModelPricing(
                input_per_million=float(pricing_raw["input_per_million"]),
                output_per_million=float(pricing_raw["output_per_million"]),
                cached_input_per_million=(
                    float(pricing_raw["cached_input_per_million"])
                    if pricing_raw.get("cached_input_per_million") is not None
                    else None
                ),
            ),
            pricing_source=pricing_source,
            pricing_checked=entry.get("pricing_checked", "unknown"),
        )

    _registry_cache = models
    return models


def get_model_spec(model_id: str) -> ModelSpec:
    """Return the registered spec for model_id, matching on exact ID or prefix.

    Provider APIs sometimes return a date-suffixed model name (e.g.
    "gpt-4.1-mini-2025-04-14"); prefix matching keeps cost/capability lookups
    working for those without listing every dated variant.
    """

    registry = _load_registry()
    if model_id in registry:
        return registry[model_id]
    for candidate_id, spec in registry.items():
        if model_id.startswith(candidate_id):
            return spec
    raise ModelRegistryError(
        f"Model '{model_id}' is not in the model registry ({MODEL_REGISTRY_PATH}). "
        "Register it before using it."
    )


def estimate_cost(model_id: str, *, input_tokens: int, output_tokens: int) -> CostEstimate:
    """Return an approximate USD cost, or a zero estimate for an unregistered model.

    Unlike get_model_spec, this never raises — cost logging must not break a
    call for an unregistered model. is_estimated=False signals the cost could
    not be resolved so callers can log it as unresolved rather than $0.00 confirmed.
    """

    try:
        spec = get_model_spec(model_id)
    except ModelRegistryError:
        logger.warning("No registry pricing for model=%s — cost reporting unresolved.", model_id)
        return CostEstimate(usd=0.0, is_estimated=False)
    usd = (
        (input_tokens / 1_000_000) * spec.pricing.input_per_million
        + (output_tokens / 1_000_000) * spec.pricing.output_per_million
    )
    return CostEstimate(usd=usd, is_estimated=True)
