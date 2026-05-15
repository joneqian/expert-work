"""Pydantic v2 schema tests for :class:`AgentSpec`."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from helix_agent.protocol import (
    AgentSpec,
    ModelSpec,
)

_MINIMAL: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {
        "name": "code-reviewer",
        "version": "1.0.0",
        "tenant": "platform-eng",
    },
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are a reviewer"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _doc() -> dict[str, Any]:
    return deepcopy(_MINIMAL)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_manifest_validates() -> None:
    spec = AgentSpec.model_validate(_doc())
    assert spec.api_version == "helix.io/v1"
    assert spec.metadata.name == "code-reviewer"
    assert spec.spec.model.provider == "anthropic"
    # Defaults should have populated.
    assert spec.spec.workflow.type == "react"
    assert spec.spec.workflow.max_iterations == 12
    assert spec.spec.dynamic_context.inject_memory is True


def test_alias_apiversion_round_trips() -> None:
    spec = AgentSpec.model_validate(_doc())
    dumped = spec.model_dump(by_alias=True)
    assert dumped["apiVersion"] == "helix.io/v1"
    assert "api_version" not in dumped


def test_fallback_chain_accepted_when_acyclic() -> None:
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {"provider": "openai", "name": "gpt-4o"},
        {"provider": "anthropic", "name": "claude-haiku-4-5"},
    ]
    spec = AgentSpec.model_validate(doc)
    assert len(spec.spec.model.fallback) == 2


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


def test_missing_model_block_rejected() -> None:
    doc = _doc()
    del doc["spec"]["model"]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert any("model" in str(err["loc"]) for err in exc_info.value.errors())


def test_invalid_kind_rejected() -> None:
    doc = _doc()
    doc["kind"] = "Workflow"
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_unknown_extra_field_rejected() -> None:
    doc = _doc()
    doc["spec"]["mystery_field"] = "boom"
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert any("mystery_field" in str(err["loc"]) for err in exc_info.value.errors())


def test_temperature_out_of_range_rejected() -> None:
    doc = _doc()
    doc["spec"]["model"]["temperature"] = 3.5
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_negative_audit_retention_rejected() -> None:
    doc = _doc()
    doc["spec"]["tenant_config"]["audit_retention_days"] = 0
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# Lint rule #7 — network allowlist != ["*"]
# ---------------------------------------------------------------------------


def test_wildcard_allowlist_rejected() -> None:
    doc = _doc()
    doc["spec"]["sandbox"]["network"]["allowlist"] = ["*"]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert "allowlist" in str(exc_info.value)


def test_explicit_wildcard_in_list_allowed() -> None:
    """A literal ``["*.internal", "*"]`` is fine — only the single-element
    ``["*"]`` is the dangerous wildcard. The list-of-one form is the only
    one we lint."""
    doc = _doc()
    doc["spec"]["sandbox"]["network"]["allowlist"] = ["*.internal", "*"]
    spec = AgentSpec.model_validate(doc)
    assert "*" in spec.spec.sandbox.network.allowlist


# ---------------------------------------------------------------------------
# Lint rule #8 — fallback chain must be acyclic
# ---------------------------------------------------------------------------


def test_self_referential_fallback_rejected() -> None:
    """Cycle: primary model recurses to itself in fallback."""
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {
            "provider": "anthropic",
            "name": "claude-sonnet-4-5",  # same as primary → cycle
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert "cycle" in str(exc_info.value).lower()


def test_deep_cycle_rejected() -> None:
    """Cycle: A → B → A through nested fallback."""
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {
            "provider": "openai",
            "name": "gpt-4o",
            "fallback": [
                {"provider": "anthropic", "name": "claude-sonnet-4-5"},  # back to root
            ],
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert "cycle" in str(exc_info.value).lower()


def test_two_providers_one_name_share_no_cycle() -> None:
    """Identity is (provider, name); same name on different provider is fine."""
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {"provider": "openai", "name": "claude-sonnet-4-5"},
    ]
    spec = AgentSpec.model_validate(doc)
    assert spec.spec.model.fallback[0].provider == "openai"


def test_model_spec_validate_directly() -> None:
    """ModelSpec works in isolation (used by the orchestrator router)."""
    model = ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o"})
    assert model.temperature == 0.2
    assert model.max_tokens == 4096


@pytest.mark.parametrize(
    "provider",
    ["kimi", "glm", "deepseek", "qwen", "doubao"],
)
def test_openai_compatible_providers_accepted(provider: str) -> None:
    """E.11.5 — five domestic OpenAI-compatible vendors must validate."""
    model = ModelSpec.model_validate({"provider": provider, "name": "test-model"})
    assert model.provider == provider


def test_unknown_provider_rejected() -> None:
    """Schema regression guard: unknown providers must still fail validation
    even after the E.11.5 Literal extension."""
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({"provider": "bedrock", "name": "claude"})


def test_rate_limit_rpm_default_is_60() -> None:
    """E.12 — ``ModelSpec.rate_limit_rpm`` defaults to a conservative
    60 rpm matching the OpenAI free-tier ballpark."""
    model = ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o"})
    assert model.rate_limit_rpm == 60


def test_rate_limit_rpm_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o", "rate_limit_rpm": 0})
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o", "rate_limit_rpm": -1})


def test_rate_limit_rpm_custom_value_accepted() -> None:
    model = ModelSpec.model_validate(
        {"provider": "kimi", "name": "moonshot-v1-128k", "rate_limit_rpm": 3}
    )
    assert model.rate_limit_rpm == 3
