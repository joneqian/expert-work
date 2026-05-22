"""Unit tests for J.10 trigger DTOs — Stream J.10 (Mini-ADR J-26 / J-42)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from helix_agent.protocol import AgentSpec, TriggerSpec

_MINIMAL: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {"name": "reporter", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you report"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _doc() -> dict[str, Any]:
    return deepcopy(_MINIMAL)


# --- TriggerSpec ----------------------------------------------------------


def test_cron_trigger_requires_expr() -> None:
    with pytest.raises(ValidationError, match=r"config\['expr'\]"):
        TriggerSpec(name="nightly", kind="cron", config={})


def test_cron_trigger_rejects_blank_expr() -> None:
    with pytest.raises(ValidationError, match=r"config\['expr'\]"):
        TriggerSpec(name="nightly", kind="cron", config={"expr": "   "})


def test_cron_trigger_accepts_expr() -> None:
    spec = TriggerSpec(name="nightly", kind="cron", config={"expr": "0 9 * * *"})
    assert spec.config["expr"] == "0 9 * * *"


def test_webhook_trigger_needs_no_expr() -> None:
    spec = TriggerSpec(name="on-push", kind="webhook", config={"seed_input": "review the push"})
    assert spec.kind == "webhook"


def test_trigger_spec_round_trips() -> None:
    spec = TriggerSpec(name="nightly", kind="cron", config={"expr": "0 9 * * *"})
    assert TriggerSpec.model_validate(spec.model_dump()) == spec


def test_trigger_spec_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TriggerSpec(name="x", kind="webhook", bogus=1)  # type: ignore[call-arg]


# --- AgentSpecBody.triggers + AgentSpec._check_triggers -------------------


def test_manifest_accepts_triggers() -> None:
    doc = _doc()
    doc["spec"]["triggers"] = [
        {"name": "nightly", "kind": "cron", "config": {"expr": "0 9 * * *"}},
        {"name": "on-push", "kind": "webhook", "config": {}},
    ]
    spec = AgentSpec.model_validate(doc)
    assert len(spec.spec.triggers) == 2


def test_manifest_rejects_duplicate_trigger_names() -> None:
    doc = _doc()
    doc["spec"]["triggers"] = [
        {"name": "nightly", "kind": "cron", "config": {"expr": "0 9 * * *"}},
        {"name": "nightly", "kind": "webhook", "config": {}},
    ]
    with pytest.raises(ValidationError, match="duplicate trigger name"):
        AgentSpec.model_validate(doc)


def test_manifest_without_triggers_defaults_empty() -> None:
    spec = AgentSpec.model_validate(_doc())
    assert spec.spec.triggers == []
