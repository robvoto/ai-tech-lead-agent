"""Per-backend coding-agent effort tiers (ATL-034).

AI Tech Lead's own plan review picks one tier (light/standard/deep) for a
task; this module is the only place that translates a tier into CLI args for
a specific configured coding-agent backend. A backend with no entry in
config/coding_agent_tier_profiles.json gets no extra args — never a guessed
flag — so adding a new coding-agent backend later is a one-entry config
change, not new branching code.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_tech_lead.config import PROJECT_ROOT
from ai_tech_lead.logging_setup import LOGGER_NAME

CODING_AGENT_TIER_PROFILES_PATH = PROJECT_ROOT / "config" / "coding_agent_tier_profiles.json"
DEFAULT_CODING_AGENT_TIER = "standard"
CODING_AGENT_TIERS: tuple[str, ...] = ("light", "standard", "deep")

logger = logging.getLogger(LOGGER_NAME)

_registry_cache: dict[str, dict[str, list[str]]] | None = None


class CodingAgentTierProfilesError(RuntimeError):
    """Raised when config/coding_agent_tier_profiles.json cannot be loaded."""


def _load_registry(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    resolved_path = path if path is not None else CODING_AGENT_TIER_PROFILES_PATH
    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CodingAgentTierProfilesError(
            f"Could not load coding-agent tier profiles from {resolved_path}: {error}"
        ) from error

    backends = raw.get("backends", {})
    if not isinstance(backends, dict):
        raise CodingAgentTierProfilesError(f"{resolved_path}: 'backends' must be an object.")

    _registry_cache = backends
    return backends


def resolve_tier_args(coding_agent_command: str, tier: str) -> tuple[str, ...]:
    """Return the extra CLI args for this backend/tier, or () if unknown.

    Never invents a flag: an unconfigured backend or an unrecognized tier both
    resolve to no extra args, running the tool at its own default.
    """

    if tier not in CODING_AGENT_TIERS:
        logger.warning(
            "Unknown coding-agent tier '%s' for backend '%s' — running with no extra args.",
            tier,
            coding_agent_command,
        )
        return ()

    backends = _load_registry()
    backend_profiles = backends.get(coding_agent_command)
    if backend_profiles is None:
        logger.info(
            "No coding-agent tier profile configured for backend '%s' — running with no extra "
            "args (tier '%s' requested).",
            coding_agent_command,
            tier,
        )
        return ()

    args = backend_profiles.get(tier)
    if args is None:
        logger.warning(
            "Backend '%s' has no configured args for tier '%s' — running with no extra args.",
            coding_agent_command,
            tier,
        )
        return ()

    return tuple(args)
