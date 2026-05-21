"""Tests for J.7a skill integration in :func:`build_agent`.

Mini-ADR J-23 § 15.4 + § 15.6 build-time validation:

* Skill resolver wires correctly with bare-name + pinned refs.
* ``<skill>`` XML wrapping lands in the assembled system prompt.
* tool_names conflict between two skills → :class:`SkillConflictError`.
* required_models mismatch → :class:`SkillModelMismatchError`.
* Not-Found / Version-Not-Found / Not-Active → distinct exception classes.
* No-skill manifest still builds (no resolver needed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from helix_agent.protocol import AgentSpec, SkillVersion
from helix_agent.runtime.checkpointer import make_checkpointer
from helix_agent.runtime.secret_store import LocalDevSecretStore
from orchestrator.agent_factory import _SkillLookupResult, build_agent
from orchestrator.errors import (
    AgentFactoryError,
    SkillConflictError,
    SkillModelMismatchError,
    SkillNotActiveError,
    SkillNotFoundError,
    SkillVersionNotFoundError,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

_ANTHROPIC_KEY_NAME = "anthropic-test"
_MINIMAL_SPEC: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {"name": "test", "version": "1.0.0", "tenant": "test-tenant"},
    "spec": {
        "tenant_config": {},
        "model": {
            "provider": "anthropic",
            "name": "claude-sonnet-4-6",
            "api_key_ref": f"secret://{_ANTHROPIC_KEY_NAME}",
        },
        "system_prompt": {"template": "you are an agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


@pytest.fixture
async def cp() -> AsyncIterator[BaseCheckpointSaver[object]]:
    async with make_checkpointer("memory") as checkpointer:
        yield checkpointer


def _spec_with_skills(skills: list[str], **model_overrides: Any) -> AgentSpec:
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["skills"] = skills
    doc["spec"]["model"].update(model_overrides)
    return AgentSpec.model_validate(doc)


def _secret_store() -> LocalDevSecretStore:
    return LocalDevSecretStore.from_mapping({_ANTHROPIC_KEY_NAME: "sk-ant-test"})


def _make_version(
    *,
    name: str = "foo",
    version: int = 1,
    prompt_fragment: str = "be helpful with X",
    tool_names: tuple[str, ...] = (),
    required_models: tuple[str, ...] = (),
) -> SkillVersion:
    return SkillVersion(
        id=uuid4(),
        skill_id=uuid4(),
        tenant_id=uuid4(),
        version=version,
        prompt_fragment=prompt_fragment,
        tool_names=tool_names,
        description=f"{name} skill",
        category=None,
        required_models=required_models,
        authored_by="human",
        created_at=datetime.now(UTC),
    )


def _make_resolver(rows: dict[tuple[str, int | None], _SkillLookupResult]) -> Any:
    """Build a resolver that returns canned ``_SkillLookupResult`` per
    (name, version) lookup. Missing keys → not_found."""

    def resolver(tenant_id: UUID, name: str, version: int | None) -> _SkillLookupResult:
        del tenant_id
        return rows.get((name, version), _SkillLookupResult.not_found())

    return resolver


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_agent_no_skills_no_resolver_works(cp: BaseCheckpointSaver[object]) -> None:
    """An empty ``spec.skills`` builds cleanly without a resolver."""
    spec = AgentSpec.model_validate(_MINIMAL_SPEC)
    built = await build_agent(spec, secret_store=_secret_store(), checkpointer=cp)
    assert built.system_prompt == "you are an agent"


@pytest.mark.asyncio
async def test_build_agent_bare_skill_ref_resolves_and_wraps_prompt(
    cp: BaseCheckpointSaver[object],
) -> None:
    spec = _spec_with_skills(["foo"])
    version = _make_version(name="foo", prompt_fragment="explain X to the user")
    resolver = _make_resolver({("foo", None): _SkillLookupResult.ok(version)})
    built = await build_agent(
        spec,
        secret_store=_secret_store(),
        checkpointer=cp,
        skill_resolver=resolver,
        tenant_id=uuid4(),
    )
    assert "you are an agent" in built.system_prompt
    assert '<skill name="foo" version="1">' in built.system_prompt
    assert "</skill>" in built.system_prompt
    assert "explain X to the user" in built.system_prompt
    assert "advisory context" in built.system_prompt


@pytest.mark.asyncio
async def test_build_agent_pinned_skill_ref_resolves(cp: BaseCheckpointSaver[object]) -> None:
    spec = _spec_with_skills(["bar@3"])
    version = _make_version(name="bar", version=3)
    resolver = _make_resolver({("bar", 3): _SkillLookupResult.ok(version)})
    built = await build_agent(
        spec,
        secret_store=_secret_store(),
        checkpointer=cp,
        skill_resolver=resolver,
        tenant_id=uuid4(),
    )
    assert '<skill name="bar" version="3">' in built.system_prompt


@pytest.mark.asyncio
async def test_build_agent_multiple_skills_preserve_declaration_order(
    cp: BaseCheckpointSaver[object],
) -> None:
    spec = _spec_with_skills(["alpha", "beta@2"])
    alpha = _make_version(name="alpha", prompt_fragment="ALPHA-BODY")
    beta = _make_version(name="beta", version=2, prompt_fragment="BETA-BODY")
    resolver = _make_resolver(
        {
            ("alpha", None): _SkillLookupResult.ok(alpha),
            ("beta", 2): _SkillLookupResult.ok(beta),
        }
    )
    built = await build_agent(
        spec,
        secret_store=_secret_store(),
        checkpointer=cp,
        skill_resolver=resolver,
        tenant_id=uuid4(),
    )
    alpha_pos = built.system_prompt.index("ALPHA-BODY")
    beta_pos = built.system_prompt.index("BETA-BODY")
    assert alpha_pos < beta_pos


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_agent_skill_without_resolver_fails(cp: BaseCheckpointSaver[object]) -> None:
    """Manifest declares a skill but build_agent has no resolver wired —
    refuse to silently ignore the skill at run time."""
    spec = _spec_with_skills(["foo"])
    with pytest.raises(AgentFactoryError, match="skill_resolver"):
        await build_agent(spec, secret_store=_secret_store(), checkpointer=cp)


@pytest.mark.asyncio
async def test_build_agent_skill_not_found_raises(cp: BaseCheckpointSaver[object]) -> None:
    spec = _spec_with_skills(["missing"])
    resolver = _make_resolver({})
    with pytest.raises(SkillNotFoundError):
        await build_agent(
            spec,
            secret_store=_secret_store(),
            checkpointer=cp,
            skill_resolver=resolver,
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_build_agent_pinned_version_missing_raises(
    cp: BaseCheckpointSaver[object],
) -> None:
    spec = _spec_with_skills(["foo@99"])
    resolver = _make_resolver({("foo", 99): _SkillLookupResult.version_not_found()})
    with pytest.raises(SkillVersionNotFoundError):
        await build_agent(
            spec,
            secret_store=_secret_store(),
            checkpointer=cp,
            skill_resolver=resolver,
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_build_agent_bare_ref_to_inactive_skill_raises(
    cp: BaseCheckpointSaver[object],
) -> None:
    spec = _spec_with_skills(["foo"])
    resolver = _make_resolver({("foo", None): _SkillLookupResult.not_active()})
    with pytest.raises(SkillNotActiveError):
        await build_agent(
            spec,
            secret_store=_secret_store(),
            checkpointer=cp,
            skill_resolver=resolver,
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_build_agent_required_models_mismatch_raises(
    cp: BaseCheckpointSaver[object],
) -> None:
    """Skill declares ``required_models`` but agent's primary model isn't in the list."""
    spec = _spec_with_skills(["foo"])
    version = _make_version(name="foo", required_models=("gpt-4o",))
    resolver = _make_resolver({("foo", None): _SkillLookupResult.ok(version)})
    with pytest.raises(SkillModelMismatchError, match="gpt-4o"):
        await build_agent(
            spec,
            secret_store=_secret_store(),
            checkpointer=cp,
            skill_resolver=resolver,
            tenant_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_build_agent_required_models_match_passes(
    cp: BaseCheckpointSaver[object],
) -> None:
    """Empty ``required_models`` skips the check; matching model passes."""
    spec = _spec_with_skills(["foo"])
    version = _make_version(name="foo", required_models=("claude-sonnet-4-6",))
    resolver = _make_resolver({("foo", None): _SkillLookupResult.ok(version)})
    built = await build_agent(
        spec,
        secret_store=_secret_store(),
        checkpointer=cp,
        skill_resolver=resolver,
        tenant_id=uuid4(),
    )
    assert built is not None


@pytest.mark.asyncio
async def test_build_agent_conflicting_tool_names_raises(
    cp: BaseCheckpointSaver[object],
) -> None:
    """Two skills declaring the same tool_name → SkillConflictError."""
    spec = _spec_with_skills(["alpha", "beta"])
    alpha = _make_version(name="alpha", tool_names=("web_search",))
    beta = _make_version(name="beta", tool_names=("web_search",))
    resolver = _make_resolver(
        {
            ("alpha", None): _SkillLookupResult.ok(alpha),
            ("beta", None): _SkillLookupResult.ok(beta),
        }
    )
    with pytest.raises(SkillConflictError, match="web_search"):
        await build_agent(
            spec,
            secret_store=_secret_store(),
            checkpointer=cp,
            skill_resolver=resolver,
            tenant_id=uuid4(),
        )
