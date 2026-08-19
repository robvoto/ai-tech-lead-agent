"""Task-aware execution profiles: the one boundary that resolves an
orchestrator call's *purpose* into a tier, then a model/reasoning-effort/
output-budget profile.

Workflow call sites request a purpose (e.g. "risk_review") — never a raw
model ID, tier, or effort string — so a future model swap, or a rebalancing
of which purposes count as cheap vs. expensive, only changes this module and
config/model_registry.json. See ATL-035 (registry) and ATL-036 (tiers,
ceilings, purpose routing, bounded override).

Tier ceilings are enforced independently of whatever a profile or the
registry's own recorded default says — a misconfigured profile cannot make
a "simple" call spend medium/high reasoning, and no automatic path can ever
select "xhigh"/"max": those require a human explicitly approving that one
run (see profile_override_store.py for the only sanctioned override path).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_tech_lead.app_settings import AppSettings
from ai_tech_lead.model_registry import ModelRegistryError, ModelSpec, get_model_spec

# --- Tiers -------------------------------------------------------------

SIMPLE_TIER = "simple"
NORMAL_TIER = "normal"
STRONG_TIER = "strong"
_TIERS = (SIMPLE_TIER, NORMAL_TIER, STRONG_TIER)

# Ordinal so a ceiling check is a simple <= comparison. "none" is the
# cheapest recognized effort; xhigh/max sit above every tier's ceiling on
# purpose — no tier's ceiling reaches them, so they can never be selected
# automatically, only through an explicit human override of that one run
# (not currently exposed — see the module docstring).
_EFFORT_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
NEVER_AUTOMATIC_EFFORTS = frozenset({"xhigh", "max"})

TIER_CEILING_EFFORT: dict[str, str] = {
    SIMPLE_TIER: "low",
    NORMAL_TIER: "medium",
    STRONG_TIER: "high",
}

# Backing profile for each tier's *current default*. This is the one place
# that changes when a migration (e.g. mini -> Luna) is adopted for a tier —
# workflow call sites never change.
#
# SIMPLE_PROFILE moved to gpt-5.6-luna @ "none" on 2026-08-19 based on a live
# benchmark (not assumed): 12 hand-labeled cases across request_relevance,
# code_look_check, telegram_intent_routing, and risk_review, run against real
# OpenAI calls. Luna @ none matched gpt-4.1-mini on every case mini got right
# and additionally got right the one deliberately ambiguous case mini missed
# (a MEDIUM-risk classification) — while costing ~43% less and answering
# faster. Luna @ low and @ medium scored the same accuracy but cost and
# latency rose with effort (medium's latency was markedly less stable, with
# two outlier calls past 7s), so "none" — not the "medium" GPT-5.6 defaults
# to when omitted — was the evidence-backed choice, matching this ticket's
# own instruction not to assume medium. See config/model_registry.json's
# pricing block for the source figures and PROJECT/ATL-036 backlog evidence
# for the raw benchmark data.
#
# Sample size caveat: 3 cases per purpose is thin for full statistical
# confidence — only the one ambiguous case per call site actually
# discriminated between models, since the rest were unambiguous enough that
# every model/effort combination got them right. Treat this as a clear
# directional signal for "simple"-tier classification/routing calls, not a
# rigorously powered study.
#
# NORMAL_PROFILE stays settings-driven (today's gpt-4.1-mini default)
# un-migrated: risk_review (mapped to "normal") showed the same promising
# signal, but that is 1 of 6 "normal"-tier purposes, and the others
# (drafting, verification, discovery) are semantically different enough that
# extrapolating from risk_review alone would be guessing, not evidence — a
# dedicated normal-tier benchmark is a scoped follow-up, not something to
# fold into this pass.
#
# STRONG_PROFILE targets Luna at "high" because ATL-035 registered it for
# validation only; it is not yet wired to tech_lead_analysis/plan_review and
# was not part of this benchmark (open-ended reasoning has no cheap
# ground-truth labels the way single-label classification does).
SIMPLE_PROFILE = "simple"
NORMAL_PROFILE = "normal"
STRONG_PROFILE = "strong"

# One purpose name per orchestrator-LLM call site. Call sites pass this
# string, never a tier or model. Classification rationale (kept here, next
# to the mapping it explains, rather than scattered per call site):
#   simple  — single-label classification / yes-or-no gate on short input;
#             wrong output is cheap to recover from (falls open/closed to a
#             safe default in the caller).
#   normal  — drafting, summarizing, or a bounded judgement call where the
#             ticket's own wording ("risk ... justifies [normal/strong]")
#             puts risk assessment here rather than in "simple".
#   strong  — the two calls that reason over the whole task end to end
#             (initial analysis, full plan correctness) rather than one
#             bounded question.
PURPOSE_TIER: dict[str, str] = {
    "telegram_intent_routing": SIMPLE_TIER,
    "request_relevance": SIMPLE_TIER,
    "code_look_check": SIMPLE_TIER,
    "project_guidance_governance": SIMPLE_TIER,
    "operator_question": NORMAL_TIER,
    "backlog_draft_builder": NORMAL_TIER,
    "completion_verification": NORMAL_TIER,
    "research_knowledge_gap_check": NORMAL_TIER,
    "research_discovery": NORMAL_TIER,
    "risk_review": NORMAL_TIER,
    "tech_lead_analysis": STRONG_TIER,
    "plan_review": STRONG_TIER,
    # Telegram's own read-only chat assistant (telegram_agent_graph.py) — a
    # bounded, tool-using conversational agent, not part of the coding
    # workflow above, but still an orchestrator call and still purpose-routed
    # rather than reading settings.orchestrator_ai_model directly.
    "telegram_chat": NORMAL_TIER,
}


class ExecutionProfileError(RuntimeError):
    """Raised when a purpose/profile is unknown, or resolves to an invalid
    model/reasoning combination, or would breach its tier's ceiling."""


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    tier: str
    model: str
    reasoning_effort: str | None
    max_output_tokens: int


# "simple" moved to Luna @ "none" per the live-benchmark evidence above.
# "normal" (settings-driven) stays on gpt-4.1-mini, which has no reasoning
# control at all — inherently within its ceiling. "strong" targets Luna at
# "high" — validated against the "strong" ceiling below, not assumed safe.
_STATIC_PROFILES: dict[str, ExecutionProfile] = {
    SIMPLE_PROFILE: ExecutionProfile(
        name=SIMPLE_PROFILE,
        tier=SIMPLE_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="none",
        max_output_tokens=200,
    ),
    STRONG_PROFILE: ExecutionProfile(
        name=STRONG_PROFILE,
        tier=STRONG_TIER,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        max_output_tokens=2000,
    ),
}


def resolve_profile_for_purpose(
    purpose: str,
    *,
    settings: AppSettings | None = None,
    min_output_tokens: int | None = None,
    override_tier: str | None = None,
) -> ExecutionProfile:
    """Resolve a call-site purpose to a validated ExecutionProfile.

    `override_tier`, when given, is the tier an active, bounded operator
    override resolved to (see profile_override_store.py) — the caller looks
    that up per purpose's normal tier and passes the result in; this
    function does not read the override store itself, keeping it a pure
    resolver.
    """

    try:
        tier = PURPOSE_TIER[purpose]
    except KeyError:
        raise ExecutionProfileError(f"Unknown orchestrator call purpose: '{purpose}'") from None

    resolved_tier = override_tier if override_tier is not None else tier
    if resolved_tier not in _TIERS:
        raise ExecutionProfileError(f"Unknown execution tier: '{resolved_tier}'")

    profile = resolve_execution_profile(
        _profile_name_for_tier(resolved_tier),
        settings=settings,
        min_output_tokens=min_output_tokens,
    )
    _validate_tier_ceiling(resolved_tier, profile)
    return profile


def resolve_execution_profile(
    name: str,
    *,
    settings: AppSettings | None = None,
    min_output_tokens: int | None = None,
) -> ExecutionProfile:
    """Resolve a profile name directly to a validated ExecutionProfile.

    Most callers should use resolve_profile_for_purpose instead; this stays
    public for the "normal" profile's settings-driven resolution and for
    tests exercising a specific profile.
    """

    if name == NORMAL_PROFILE:
        if settings is None:
            raise ExecutionProfileError(
                "settings is required to resolve the 'normal' execution profile"
            )
        profile = _normal_profile(settings)
    else:
        try:
            profile = _STATIC_PROFILES[name]
        except KeyError:
            raise ExecutionProfileError(f"Unknown execution profile: '{name}'") from None

    if min_output_tokens is not None and min_output_tokens > profile.max_output_tokens:
        profile = replace(profile, max_output_tokens=min_output_tokens)

    _validate_profile(profile)
    return profile


def _profile_name_for_tier(tier: str) -> str:
    return {SIMPLE_TIER: SIMPLE_PROFILE, NORMAL_TIER: NORMAL_PROFILE, STRONG_TIER: STRONG_PROFILE}[
        tier
    ]


def _normal_profile(settings: AppSettings) -> ExecutionProfile:
    try:
        spec = get_model_spec(settings.orchestrator_ai_model)
    except ModelRegistryError as error:
        raise ExecutionProfileError(str(error)) from error
    reasoning_effort = spec.reasoning.default_effort if spec.reasoning is not None else None
    return ExecutionProfile(
        name=NORMAL_PROFILE,
        tier=NORMAL_TIER,
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


def _validate_tier_ceiling(tier: str, profile: ExecutionProfile) -> None:
    """Enforce the tier's hard reasoning-effort ceiling independent of the
    profile's own declared effort or the model's registry default — the
    safety net acceptance criterion 12 (ATL-036) asks for."""

    effort = profile.reasoning_effort
    if effort is None:
        return
    if effort in NEVER_AUTOMATIC_EFFORTS:
        raise ExecutionProfileError(
            f"Reasoning effort '{effort}' is never selected automatically "
            f"(profile '{profile.name}', tier '{tier}'); it requires an explicit "
            "human approval for that one run."
        )
    ceiling = TIER_CEILING_EFFORT[tier]
    if _EFFORT_ORDER[effort] > _EFFORT_ORDER[ceiling]:
        raise ExecutionProfileError(
            f"Profile '{profile.name}' resolves to reasoning effort '{effort}', "
            f"which exceeds tier '{tier}''s ceiling of '{ceiling}'."
        )


def next_escalation_effort(tier: str, current_effort: str | None) -> str | None:
    """Return the next reasoning effort one step up from current_effort,
    bounded to `tier`'s own ceiling — never crossing into another tier.

    Returns None when there is no room left to escalate within the tier
    (already at, or above, the ceiling, or the model has no reasoning
    control at all). Used by llm_json.py's bounded one-step retry escalation
    (ATL-036 acceptance criterion 13); further escalation is not automatic.
    """

    if current_effort is None:
        return None
    ceiling = TIER_CEILING_EFFORT[tier]
    ceiling_level = _EFFORT_ORDER[ceiling]
    current_level = _EFFORT_ORDER[current_effort]
    if current_level >= ceiling_level:
        return None
    next_level = current_level + 1
    for effort, level in _EFFORT_ORDER.items():
        if level == next_level:
            return effort
    return None


def validate_registry_ceilings() -> None:
    """Fail closed at startup if any tier's default profile would breach its
    own ceiling — an invalid or newly-unsupported profile must never fall
    back to a stronger model silently (ATL-036 acceptance criterion 15)."""

    for tier in _TIERS:
        profile = _STATIC_PROFILES.get(_profile_name_for_tier(tier))
        if profile is None:
            # "normal" is settings-driven and validated per-call instead —
            # nothing static to check at startup without settings in hand.
            continue
        _validate_tier_ceiling(tier, profile)
