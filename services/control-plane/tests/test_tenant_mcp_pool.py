"""Unit tests for the per-tenant remote MCP pool service (Stream V-D)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from control_plane.tenant_mcp_pool import TenantMcpPoolService
from helix_agent.persistence import InMemoryTenantMcpServerStore
from orchestrator.tools.mcp import MCPServerConfig, MCPToolDef, RecordingMCPClient


def _client_factory_spy(calls: list[str]):
    async def _factory(config: MCPServerConfig):
        calls.append(config.name)
        return RecordingMCPClient(tools=(MCPToolDef(name="t", description="", input_schema={}),))

    return _factory


async def _seed(store: InMemoryTenantMcpServerStore, tenant_id, name="github", enabled=True):
    rec = await store.create(
        tenant_id=tenant_id,
        name=name,
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        auth_type="none",
        token_secret_ref=None,
        timeout_s=30.0,
        created_by="a@x",
    )
    if not enabled:
        from helix_agent.protocol import TenantMcpServerPatch

        await store.update(
            tenant_id=tenant_id,
            name=name,
            patch=TenantMcpServerPatch(enabled=False),
        )
    return rec


@pytest.mark.asyncio
async def test_builds_pool_from_enabled_servers() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    calls: list[str] = []
    svc = TenantMcpPoolService(
        store=store, secret_store=None, client_factory=_client_factory_spy(calls)
    )
    pool = await svc.get_or_build(tid)
    assert pool.names() == ["github"]
    assert calls == ["github"]


@pytest.mark.asyncio
async def test_disabled_servers_excluded() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github", enabled=True)
    await _seed(store, tid, "linear", enabled=False)
    svc = TenantMcpPoolService(
        store=store, secret_store=None, client_factory=_client_factory_spy([])
    )
    pool = await svc.get_or_build(tid)
    assert pool.names() == ["github"]


@pytest.mark.asyncio
async def test_second_call_returns_cached_pool_no_rebuild() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    calls: list[str] = []
    svc = TenantMcpPoolService(
        store=store, secret_store=None, client_factory=_client_factory_spy(calls)
    )
    p1 = await svc.get_or_build(tid)
    p2 = await svc.get_or_build(tid)
    assert p1 is p2
    assert calls == ["github"]  # built once


@pytest.mark.asyncio
async def test_invalidate_closes_and_rebuilds() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    calls: list[str] = []
    svc = TenantMcpPoolService(
        store=store, secret_store=None, client_factory=_client_factory_spy(calls)
    )
    p1 = await svc.get_or_build(tid)
    await svc.invalidate(tid)
    p2 = await svc.get_or_build(tid)
    assert p1 is not p2
    assert calls == ["github", "github"]  # rebuilt


@pytest.mark.asyncio
async def test_empty_when_no_servers() -> None:
    store = InMemoryTenantMcpServerStore()
    svc = TenantMcpPoolService(
        store=store, secret_store=None, client_factory=_client_factory_spy([])
    )
    pool = await svc.get_or_build(uuid4())
    assert pool.names() == []


@pytest.mark.asyncio
async def test_close_all_clears_cache() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _seed(store, tid, "github")
    svc = TenantMcpPoolService(
        store=store, secret_store=None, client_factory=_client_factory_spy([])
    )
    await svc.get_or_build(tid)
    await svc.close_all()
    # after close_all a fresh build is required again (cache cleared)
    assert svc._pools == {}
