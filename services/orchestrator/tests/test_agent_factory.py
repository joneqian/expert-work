"""Unit tests for the agent factory — manifest → runnable agent."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from langgraph.graph.state import CompiledStateGraph

from helix_agent.protocol import AgentSpec, ModelSpec
from helix_agent.runtime.checkpointer import make_checkpointer
from helix_agent.runtime.middleware import RecordingLangfuseClient
from helix_agent.runtime.secret_store import LocalDevSecretStore
from orchestrator import (
    AgentFactoryError,
    AnthropicProvider,
    BuiltAgent,
    LLMRouter,
    MiddlewareEnv,
    OpenAIProvider,
    ToolEnv,
    build_agent,
    build_llm_router,
)
from orchestrator.llm import RateLimitedProvider
from orchestrator.tools import RecordingTavilyClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ANTHROPIC_KEY_NAME = "helix-agent/dev/llm/anthropic"
_OPENAI_KEY_NAME = "helix-agent/dev/llm/openai"
_KIMI_KEY_NAME = "helix-agent/dev/llm/kimi"

_MINIMAL_SPEC: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {"name": "test-agent", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {
            "provider": "anthropic",
            "name": "claude-sonnet-4-6",
            "api_key_ref": f"secret://{_ANTHROPIC_KEY_NAME}",
        },
        "system_prompt": {"template": "you are a test agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(**model_overrides: Any) -> AgentSpec:
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["model"].update(model_overrides)
    return AgentSpec.model_validate(doc)


def _secret_store() -> LocalDevSecretStore:
    return LocalDevSecretStore.from_mapping(
        {
            _ANTHROPIC_KEY_NAME: "sk-ant-test",
            _OPENAI_KEY_NAME: "sk-openai-test",
            _KIMI_KEY_NAME: "sk-kimi-test",
        }
    )


# ---------------------------------------------------------------------------
# build_llm_router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_llm_router_single_anthropic_provider() -> None:
    router = await build_llm_router(_spec().spec.model, secret_store=_secret_store())
    assert isinstance(router, LLMRouter)
    assert len(router.providers) == 1
    handle = router.providers[0]
    assert handle.key == "anthropic:claude-sonnet-4-6"
    # The provider is rate-limited (E.12) wrapping the real adapter.
    assert isinstance(handle.provider, RateLimitedProvider)
    assert isinstance(handle.provider.inner, AnthropicProvider)


@pytest.mark.asyncio
async def test_build_llm_router_resolves_key_from_secret_store() -> None:
    """The api_key_ref is resolved through the SecretStore — the
    resolved value reaches the provider's HTTP client."""
    router = await build_llm_router(_spec().spec.model, secret_store=_secret_store())
    inner = router.providers[0].provider.inner
    assert isinstance(inner, AnthropicProvider)
    # HTTPAnthropicClient holds the resolved key.
    assert inner.client.api_key == "sk-ant-test"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_build_llm_router_flattens_fallback_chain_preorder() -> None:
    model = _spec(
        fallback=[
            {
                "provider": "openai",
                "name": "gpt-4o",
                "api_key_ref": f"secret://{_OPENAI_KEY_NAME}",
            },
            {
                "provider": "kimi",
                "name": "moonshot-v1-128k",
                "api_key_ref": f"secret://{_KIMI_KEY_NAME}",
            },
        ]
    ).spec.model
    router = await build_llm_router(model, secret_store=_secret_store())
    assert [h.key for h in router.providers] == [
        "anthropic:claude-sonnet-4-6",
        "openai:gpt-4o",
        "kimi:moonshot-v1-128k",
    ]


@pytest.mark.asyncio
async def test_build_llm_router_nested_fallback_preorder() -> None:
    """A fallback that itself has a fallback flattens depth-first."""
    model = _spec(
        fallback=[
            {
                "provider": "openai",
                "name": "gpt-4o",
                "api_key_ref": f"secret://{_OPENAI_KEY_NAME}",
                "fallback": [
                    {
                        "provider": "kimi",
                        "name": "moonshot-v1-128k",
                        "api_key_ref": f"secret://{_KIMI_KEY_NAME}",
                    }
                ],
            },
        ]
    ).spec.model
    router = await build_llm_router(model, secret_store=_secret_store())
    assert [h.key for h in router.providers] == [
        "anthropic:claude-sonnet-4-6",
        "openai:gpt-4o",
        "kimi:moonshot-v1-128k",
    ]


@pytest.mark.asyncio
async def test_build_llm_router_rate_limit_from_model_spec() -> None:
    model = _spec(rate_limit_rpm=3).spec.model
    router = await build_llm_router(model, secret_store=_secret_store())
    limiter = router.providers[0].provider.limiter  # type: ignore[union-attr]
    assert limiter.max_rate == 3


@pytest.mark.asyncio
async def test_build_llm_router_missing_api_key_ref_raises() -> None:
    model = ModelSpec.model_validate({"provider": "anthropic", "name": "claude"})
    with pytest.raises(AgentFactoryError, match="no api_key_ref"):
        await build_llm_router(model, secret_store=_secret_store())


@pytest.mark.asyncio
async def test_build_llm_router_unsupported_provider_raises() -> None:
    model = ModelSpec.model_validate(
        {
            "provider": "azure",
            "name": "gpt-4o",
            "api_key_ref": f"secret://{_OPENAI_KEY_NAME}",
        }
    )
    with pytest.raises(AgentFactoryError, match="no adapter yet"):
        await build_llm_router(model, secret_store=_secret_store())


@pytest.mark.asyncio
async def test_build_llm_router_missing_secret_propagates() -> None:
    """A ref pointing at a non-existent secret surfaces the
    SecretStore's error, not a silent empty key."""
    model = _spec(api_key_ref="secret://does/not/exist").spec.model
    with pytest.raises(KeyError):
        await build_llm_router(model, secret_store=_secret_store())


@pytest.mark.asyncio
async def test_build_llm_router_openai_compatible_vendor() -> None:
    model = _spec(
        provider="kimi",
        name="moonshot-v1-128k",
        api_key_ref=f"secret://{_KIMI_KEY_NAME}",
    ).spec.model
    router = await build_llm_router(model, secret_store=_secret_store())
    inner = router.providers[0].provider.inner  # type: ignore[union-attr]
    # Domestic vendors reuse OpenAIProvider (E.11.5).
    assert isinstance(inner, OpenAIProvider)


# ---------------------------------------------------------------------------
# build_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_agent_returns_built_agent() -> None:
    async with make_checkpointer("memory") as cp:
        built = await build_agent(_spec(), secret_store=_secret_store(), checkpointer=cp)
    assert isinstance(built, BuiltAgent)
    assert isinstance(built.graph, CompiledStateGraph)
    assert built.system_prompt == "you are a test agent"
    # Default WorkflowSpec.max_iterations.
    assert built.max_steps == 12


@pytest.mark.asyncio
async def test_build_agent_max_steps_from_workflow() -> None:
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["workflow"] = {"type": "react", "max_iterations": 5}
    spec = AgentSpec.model_validate(doc)
    async with make_checkpointer("memory") as cp:
        built = await build_agent(spec, secret_store=_secret_store(), checkpointer=cp)
    assert built.max_steps == 5


@pytest.mark.asyncio
async def test_build_agent_missing_key_raises() -> None:
    doc = deepcopy(_MINIMAL_SPEC)
    del doc["spec"]["model"]["api_key_ref"]
    spec = AgentSpec.model_validate(doc)
    async with make_checkpointer("memory") as cp:
        with pytest.raises(AgentFactoryError, match="no api_key_ref"):
            await build_agent(spec, secret_store=_secret_store(), checkpointer=cp)


def _spec_with_web_search() -> AgentSpec:
    doc = deepcopy(_MINIMAL_SPEC)
    doc["spec"]["tools"] = [{"type": "builtin", "name": "web_search"}]
    return AgentSpec.model_validate(doc)


@pytest.mark.asyncio
async def test_build_agent_tool_declared_without_env_raises() -> None:
    """A manifest tool whose ToolEnv dep is absent fails at build time."""
    spec = _spec_with_web_search()
    async with make_checkpointer("memory") as cp:
        with pytest.raises(AgentFactoryError, match="Tavily client"):
            await build_agent(spec, secret_store=_secret_store(), checkpointer=cp)


@pytest.mark.asyncio
async def test_build_agent_with_tool_env_succeeds() -> None:
    """``tool_env`` is threaded to the assembler — a satisfied dep builds."""
    spec = _spec_with_web_search()
    env = ToolEnv(web_search_client=RecordingTavilyClient())
    async with make_checkpointer("memory") as cp:
        built = await build_agent(spec, secret_store=_secret_store(), checkpointer=cp, tool_env=env)
    assert isinstance(built, BuiltAgent)


@pytest.mark.asyncio
async def test_build_agent_with_middleware_env_succeeds() -> None:
    """``middleware_env`` is threaded to build_middleware_chains."""
    env = MiddlewareEnv(langfuse_client=RecordingLangfuseClient())
    async with make_checkpointer("memory") as cp:
        built = await build_agent(
            _spec(), secret_store=_secret_store(), checkpointer=cp, middleware_env=env
        )
    assert isinstance(built, BuiltAgent)
