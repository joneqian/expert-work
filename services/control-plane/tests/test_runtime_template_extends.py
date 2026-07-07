"""Build-time template-extends resolution — Stream Agent-Templates (M1-3).

Covers ``_resolve_template_extends``: the glue that makes the tier① security
floor actually fire at build time. Fail-closed posture is the security contract —
an ``extends`` manifest must never build un-floored.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from control_plane.runtime import _resolve_template_extends
from expert_work.protocol import AgentSpec
from orchestrator.errors import AgentFactoryError

_BASE_DOC: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "support-bot", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are support"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(*, extends: str | None = None, **spec_over: Any) -> AgentSpec:
    doc = deepcopy(_BASE_DOC)
    if extends is not None:
        doc["spec"]["extends"] = extends
    doc["spec"].update(spec_over)
    return AgentSpec.model_validate(doc)


@pytest.mark.asyncio
async def test_no_extends_returns_spec_unchanged() -> None:
    spec = _spec()
    resolved = await _resolve_template_extends(spec, None)
    assert resolved is spec


@pytest.mark.asyncio
async def test_extends_without_resolver_fails_closed() -> None:
    spec = _spec(extends="support-bot@1.0.0")
    with pytest.raises(AgentFactoryError, match="not configured"):
        await _resolve_template_extends(spec, None)


@pytest.mark.asyncio
async def test_missing_base_template_fails_closed() -> None:
    spec = _spec(extends="support-bot@9.9.9")

    async def resolver(_name: str, _version: str) -> AgentSpec | None:
        return None

    with pytest.raises(AgentFactoryError, match="not found"):
        await _resolve_template_extends(spec, resolver)


@pytest.mark.asyncio
async def test_malformed_extends_ref_fails_closed() -> None:
    spec = _spec(extends="no-at-sign")

    async def resolver(_name: str, _version: str) -> AgentSpec | None:
        return _spec()

    with pytest.raises(AgentFactoryError, match="invalid extends ref"):
        await _resolve_template_extends(spec, resolver)


@pytest.mark.asyncio
async def test_floor_enforced_and_extends_cleared() -> None:
    base = _spec(defenses={"output_screen": "block"})
    # Fork tries to weaken the inherited defense to off.
    fork = _spec(extends="support-bot@1.0.0", defenses={"output_screen": "off"})

    captured: dict[str, str] = {}

    async def resolver(name: str, version: str) -> AgentSpec | None:
        captured["name"], captured["version"] = name, version
        return base

    resolved = await _resolve_template_extends(fork, resolver)
    # Floor re-asserted (block wins) + link cleared → plain build-ready spec.
    assert resolved.spec.defenses.output_screen == "block"
    assert resolved.spec.extends is None
    assert captured == {"name": "support-bot", "version": "1.0.0"}


@pytest.mark.asyncio
async def test_latest_version_passed_through_to_resolver() -> None:
    fork = _spec(extends="support-bot@latest")
    seen: dict[str, str] = {}

    async def resolver(name: str, version: str) -> AgentSpec | None:
        seen["version"] = version
        return _spec()

    await _resolve_template_extends(fork, resolver)
    assert seen["version"] == "latest"
