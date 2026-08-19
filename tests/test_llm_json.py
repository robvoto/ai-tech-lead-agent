from __future__ import annotations

import pytest

from ai_tech_lead.llm_json import call_llm_for_json, parse_json_object, strip_json_fence
from ai_tech_lead.orchestrator_llm import OrchestratorLlmConfig, OrchestratorLlmResult


def _config() -> OrchestratorLlmConfig:
    return OrchestratorLlmConfig(model="test:model", max_output_tokens=100, timeout_seconds=5)


def test_strip_json_fence_removes_a_single_enclosing_fence() -> None:
    assert strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_json_fence_leaves_plain_json_unchanged() -> None:
    assert strip_json_fence('{"a": 1}') == '{"a": 1}'


def test_parse_json_object_rejects_a_non_object_payload() -> None:
    with pytest.raises(TypeError, match="must be a JSON object"):
        parse_json_object("[1, 2, 3]", error_label="test response")


def test_call_llm_for_json_returns_the_parsed_value_on_first_try(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(text='{"ok": true}'),
    )

    value = call_llm_for_json(
        prompt="p", config=_config(), error_label="test", parse=lambda payload: payload["ok"]
    )

    assert value is True


def test_call_llm_for_json_retries_once_then_raises_the_last_error(monkeypatch) -> None:
    calls: list[int] = []

    def fake_call(**_kw):
        calls.append(1)
        return OrchestratorLlmResult(text="not json")

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    with pytest.raises(Exception):
        call_llm_for_json(
            prompt="p", config=_config(), error_label="test", parse=lambda payload: payload
        )

    assert len(calls) == 2


def test_call_llm_for_json_retries_when_parse_itself_rejects_the_shape(monkeypatch) -> None:
    """A syntactically valid JSON object that fails the caller's own validation
    (parse raising) must also trigger a retry, not just a raw JSON parse failure."""
    calls: list[int] = []

    def fake_call(**_kw):
        calls.append(1)
        if len(calls) == 1:
            return OrchestratorLlmResult(text='{"ok": "not-a-bool"}')
        return OrchestratorLlmResult(text='{"ok": true}')

    def _parse(payload):
        if not isinstance(payload["ok"], bool):
            raise ValueError("ok must be boolean")
        return payload["ok"]

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    value = call_llm_for_json(prompt="p", config=_config(), error_label="test", parse=_parse)

    assert len(calls) == 2
    assert value is True


def test_call_llm_for_json_escalates_reasoning_effort_within_tier_on_retry(monkeypatch) -> None:
    seen_configs: list[OrchestratorLlmConfig] = []

    def fake_call(*, prompt, config):
        seen_configs.append(config)
        if len(seen_configs) == 1:
            return OrchestratorLlmResult(text="not json")
        return OrchestratorLlmResult(text='{"ok": true}')

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    config = OrchestratorLlmConfig(
        model="test:model", max_output_tokens=100, timeout_seconds=5, reasoning_effort="none"
    )
    value = call_llm_for_json(
        prompt="p", config=config, error_label="test", parse=lambda payload: payload["ok"],
        tier="simple",
    )

    assert value is True
    assert seen_configs[0].reasoning_effort == "none"
    assert seen_configs[1].reasoning_effort == "low"


def test_call_llm_for_json_without_tier_never_escalates(monkeypatch) -> None:
    seen_configs: list[OrchestratorLlmConfig] = []

    def fake_call(*, prompt, config):
        seen_configs.append(config)
        return OrchestratorLlmResult(text="not json")

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    config = OrchestratorLlmConfig(
        model="test:model", max_output_tokens=100, timeout_seconds=5, reasoning_effort="none"
    )
    with pytest.raises(Exception):
        call_llm_for_json(
            prompt="p", config=config, error_label="test", parse=lambda payload: payload
        )

    assert seen_configs[0].reasoning_effort == "none"
    assert seen_configs[1].reasoning_effort == "none"


def test_call_llm_for_json_escalation_never_exceeds_tier_ceiling(monkeypatch) -> None:
    """"simple"'s ceiling is "low" — already-at-ceiling must not climb to medium."""
    seen_configs: list[OrchestratorLlmConfig] = []

    def fake_call(*, prompt, config):
        seen_configs.append(config)
        return OrchestratorLlmResult(text="not json")

    monkeypatch.setattr("ai_tech_lead.llm_json.call_orchestrator_llm", fake_call)

    config = OrchestratorLlmConfig(
        model="test:model", max_output_tokens=100, timeout_seconds=5, reasoning_effort="low"
    )
    with pytest.raises(Exception):
        call_llm_for_json(
            prompt="p", config=config, error_label="test", parse=lambda payload: payload,
            tier="simple",
        )

    assert seen_configs[0].reasoning_effort == "low"
    assert seen_configs[1].reasoning_effort == "low"


def test_call_llm_for_json_calls_on_result_for_every_attempt(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_tech_lead.llm_json.call_orchestrator_llm",
        lambda **_kw: OrchestratorLlmResult(text="not json"),
    )
    seen: list[str] = []

    with pytest.raises(Exception):
        call_llm_for_json(
            prompt="p",
            config=_config(),
            error_label="test",
            parse=lambda payload: payload,
            on_result=lambda result: seen.append(result.text),
        )

    assert seen == ["not json", "not json"]
