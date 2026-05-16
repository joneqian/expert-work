"""Tests for the E.1 checkpointer wiring — settings + lifespan swap.

The Postgres checkpointer itself is integration-tested by
``test_checkpointer_factory.py`` in helix-runtime. Here we cover the
control-plane wiring: settings defaults, the ``make_agent_builder``
seam, and the lifespan branch that swaps in the durable checkpointer.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.app import create_app
from control_plane.runtime import (
    AgentRuntime,
    build_middleware_env,
    build_tool_env,
    make_agent_builder,
    make_agent_runtime,
)
from control_plane.settings import Settings
from control_plane.tenancy import TenantConfigNotConfiguredError
from helix_agent.runtime.secret_store import LocalDevSecretStore
from tests.auth_fixtures import build_test_jwt_verifier


def _secret_store() -> LocalDevSecretStore:
    return LocalDevSecretStore.from_mapping({})


def test_checkpointer_settings_default_to_memory() -> None:
    settings = Settings()
    assert settings.checkpointer_backend == "memory"
    assert settings.checkpointer_dsn is None


def test_make_agent_builder_returns_distinct_callables() -> None:
    store = _secret_store()
    a = make_agent_builder(store, InMemorySaver())
    b = make_agent_builder(store, InMemorySaver())
    assert callable(a)
    assert callable(b)
    assert a is not b


def test_make_agent_runtime_builds_runtime() -> None:
    runtime = make_agent_runtime(_secret_store())
    assert isinstance(runtime, AgentRuntime)
    assert callable(runtime.agent_builder)


@pytest.mark.asyncio
async def test_lifespan_postgres_without_dsn_raises() -> None:
    """``checkpointer_backend='postgres'`` with no DSN fails fast at boot."""
    settings = Settings(checkpointer_backend="postgres", checkpointer_dsn=None)
    app = create_app(
        settings=settings,
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    with pytest.raises(RuntimeError, match="checkpointer_dsn"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - lifespan raises before yield


@pytest.mark.asyncio
async def test_lifespan_swaps_builder_to_inject_envs() -> None:
    """Lifespan rebuilds the agent builder with the tool / middleware
    env bundles — the create_app-body placeholder is replaced."""
    settings = Settings(checkpointer_backend="memory")
    app = create_app(
        settings=settings,
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    builder_before = app.state.agent_runtime.agent_builder
    async with app.router.lifespan_context(app):
        assert app.state.agent_runtime.agent_builder is not builder_before


# ---------------------------------------------------------------------------
# ToolEnv / MiddlewareEnv backends (PR1.5)
# ---------------------------------------------------------------------------


@dataclass
class _FakeTenantConfigService:
    """Minimal stand-in — ``build_tool_env``'s provider only calls ``get``."""

    allowlist: list[str] | None  # None → the tenant has no config row

    async def get(self, *, tenant_id: UUID, actor_id: str | None = None) -> object:
        if self.allowlist is None:
            raise TenantConfigNotConfiguredError(tenant_id=tenant_id)
        return SimpleNamespace(http_tool_allowlist=self.allowlist)


def test_build_middleware_env_wires_cache_and_langfuse() -> None:
    env = build_middleware_env()
    assert env.response_cache is not None
    assert env.langfuse_client is not None


def test_build_tool_env_wires_allowlist_provider_only() -> None:
    env = build_tool_env(_FakeTenantConfigService(allowlist=[]))
    assert env.allowlist_provider is not None
    # web_search (Tavily) and mcp pool are deferred follow-ups.
    assert env.web_search_client is None
    assert env.mcp_pool is None


@pytest.mark.asyncio
async def test_allowlist_provider_returns_tenant_patterns() -> None:
    env = build_tool_env(_FakeTenantConfigService(allowlist=["https://api.github.com/*"]))
    assert env.allowlist_provider is not None
    assert await env.allowlist_provider(uuid4()) == ["https://api.github.com/*"]


@pytest.mark.asyncio
async def test_allowlist_provider_empty_for_headerless_tenant() -> None:
    env = build_tool_env(_FakeTenantConfigService(allowlist=["x"]))
    assert env.allowlist_provider is not None
    assert await env.allowlist_provider(None) == []


@pytest.mark.asyncio
async def test_allowlist_provider_empty_when_tenant_unconfigured() -> None:
    env = build_tool_env(_FakeTenantConfigService(allowlist=None))
    assert env.allowlist_provider is not None
    assert await env.allowlist_provider(uuid4()) == []
