"""Named execution profiles: the one boundary that resolves a purpose into a
model, reasoning effort, and output budget.

Workflow call sites request a profile by name — never a raw model ID or
effort string — so a future model swap only changes this module and
config/model_registry.json, not call sites. See ATL-035.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.model_registry import ModelRegistryError, ModelSpec, get_model_spec

STANDARD_PROFILE = "standard"
LOW_PROFILE = "low"
DEEP_PROFILE = "deep"


class ExecutionProfileError(RuntimeError):
    """Raised when a profile is unknown or resolves to an invalid model/reasoning combination."""


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    model: str
    reasoning_effort: str | None
    max_output_tokens: int


# Static profiles are not settings-dependent: they exist now so the profile
# mechanism is testable end-to-end, and so ATL-036 has cheaper/stronger
# options to route to later. No existing call site is wired to these yet —
# reassigning today's call sites to a non-default profile is a deliberate
# product decision left for a follow-up, not guessed here.
_STATIC_PROFILES: dict[str, ExecutionProfile] = {
    LOW_PROFILE: ExecutionProfile(
        name=LOW_PROFILE, model="gpt-4.1-mini", reasoning_effort=None, max_output_tokens=200
    ),
    DEEP_PROFILE: ExecutionProfile(
        name=DEEP_PROFILE,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        max_output_tokens=2000,
    ),
}


def resolve_execution_profile(
    name: str,
    *,
    settings: AppSettings | None = None,
    min_output_tokens: int | None = None,
) -> ExecutionProfile:
    """Resolve a profile name to a validated ExecutionProfile.

    "standard" is dynamic: it mirrors settings.orchestrator_ai_model so
    config-driven model selection (per project standards) keeps working.
    Other profile names are fixed definitions below.
    """

    if name == STANDARD_PROFILE:
        if settings is None:
            raise ExecutionProfileError(
                "settings is required to resolve the 'standard' execution profile"
            )
        profile = _standard_profile(settings)
    else:
        try:
            profile = _STATIC_PROFILES[name]
        except KeyError:
            raise ExecutionProfileError(f"Unknown execution profile: '{name}'") from None

    if min_output_tokens is not None and min_output_tokens > profile.max_output_tokens:
        profile = replace(profile, max_output_tokens=min_output_tokens)

    _validate_profile(profile)
    return profile


def _standard_profile(settings: AppSettings) -> ExecutionProfile:
    try:
        spec = get_model_spec(settings.orchestrator_ai_model)
    except ModelRegistryError as error:
        raise ExecutionProfileError(str(error)) from error
    reasoning_effort = spec.reasoning.default_effort if spec.reasoning is not None else None
    return ExecutionProfile(
        name=STANDARD_PROFILE,
        model=settings.orchestrator_ai_model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=settings.orchestrator_ai_max_output_tokens,
    )


def _validate_profile(profile: ExecutionProfile) -> None:
    try:
        spec: ModelSpec = get_model_spec(profile.model)
    except ModelRegistryError as error:
        raise ExecutionProfileError(str(error)) from error

    if profile.reasoning_effort is None:
        if spec.reasoning is not None and spec.reasoning.requires_explicit_effort:
            raise ExecutionProfileError(
                f"Model '{profile.model}' requires an explicit reasoning effort; "
                f"profile '{profile.name}' did not set one."
            )
        return

    if spec.reasoning is None:
        raise ExecutionProfileError(
            f"Model '{profile.model}' does not support a reasoning effort parameter; "
            f"profile '{profile.name}' set reasoning_effort='{profile.reasoning_effort}'."
        )
    if profile.reasoning_effort not in spec.reasoning.supported_efforts:
        raise ExecutionProfileError(
            f"Model '{profile.model}' does not support reasoning effort "
            f"'{profile.reasoning_effort}'. Supported: {spec.reasoning.supported_efforts}."
        )
    if (
        profile.reasoning_effort != "none"
        and profile.max_output_tokens < spec.reasoning.min_recommended_output_tokens
    ):
        raise ExecutionProfileError(
            f"Profile '{profile.name}' sets max_output_tokens="
            f"{profile.max_output_tokens} for model '{profile.model}', below the "
            f"recommended floor of {spec.reasoning.min_recommended_output_tokens} — "
            "reasoning tokens can consume the output budget before visible text."
        )
