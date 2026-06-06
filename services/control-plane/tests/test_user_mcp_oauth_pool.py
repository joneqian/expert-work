"""Unit tests for the per-(tenant, user) MCP OAuth pool — Stream MCP-OAUTH (OA-3b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from control_plane.tenant_scope import bypass_rls_session
from control_plane.user_mcp_oauth_pool import McpClientFactory, UserMcpOAuthPoolService
from helix_agent.persistence import (
    InMemoryMcpConnectorCatalogStore,
    InMemoryMcpOAuthConnectionStore,
)
from helix_agent.protocol import (
    McpConnectorAuthSchema,
    McpConnectorCatalogUpsert,
    McpOAuthConnectionPatch,
)
from orchestrator.tools.mcp import MCPServerConfig, MCPToolDef, RecordingMCPClient

_NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)


def _factory_spy(calls: list[str]) -> McpClientFactory:
    async def _factory(config: MCPServerConfig) -> RecordingMCPClient:
        calls.append(config.name)
        return RecordingMCPClient(tools=(MCPToolDef(name="t", description="", input_schema={}),))

    return _factory


async def _seed_catalog(store: InMemoryMcpConnectorCatalogStore, *, name: str = "linear") -> UUID:
    async with bypass_rls_session():
        rec = await store.create(
            upsert=McpConnectorCatalogUpsert(
                name=name,
                display_name="Linear",
                transport="sse",
                url_template="https://mcp.linear.app/sse",
                auth_type="oauth2",
                auth_schema=McpConnectorAuthSchema(),
                oauth_client_id="cid",
                oauth_scopes="read",
            ),
            actor_id="seed",
        )
    return rec.id


async def _seed_connection(
    store: InMemoryMcpOAuthConnectionStore,
    *,
    tenant_id: UUID,
    user_id: str,
    catalog_id: UUID,
    name: str = "linear",
    status: str = "connected",
    expires_at: datetime | None = None,
) -> None:
    rec = await store.create(
        tenant_id=tenant_id,
        user_id=user_id,
        catalog_id=catalog_id,
        name=name,
        resolved_url="https://mcp.linear.app/sse",
        oauth_state="st",
        pkce_verifier="pv",
    )
    if status == "connected":
        await store.update(
            connection_id=rec.id,
            tenant_id=tenant_id,
            user_id=user_id,
            patch=McpOAuthConnectionPatch(
                status="connected",
                access_token_ref=f"secret://helix-agent/tenant/{tenant_id}/mcp-oauth/{rec.id}/access",
                token_expires_at=expires_at,
                clear_flow_state=True,
            ),
        )


def _svc(
    oauth_store: InMemoryMcpOAuthConnectionStore,
    catalog_store: InMemoryMcpConnectorCatalogStore,
    calls: list[str],
) -> UserMcpOAuthPoolService:
    return UserMcpOAuthPoolService(
        oauth_store=oauth_store,
        catalog_store=catalog_store,
        client_factory=_factory_spy(calls),
        clock=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_connected_unexpired_in_pool() -> None:
    cat_store = InMemoryMcpConnectorCatalogStore()
    oauth_store = InMemoryMcpOAuthConnectionStore()
    tid, uid = uuid4(), "user-1"
    cat_id = await _seed_catalog(cat_store)
    await _seed_connection(
        oauth_store,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(hours=1),
    )
    calls: list[str] = []
    pool = await _svc(oauth_store, cat_store, calls).get_or_build(tid, uid)
    assert pool.names() == ["linear"]
    assert calls == ["linear"]


@pytest.mark.asyncio
async def test_expired_token_skipped() -> None:
    cat_store = InMemoryMcpConnectorCatalogStore()
    oauth_store = InMemoryMcpOAuthConnectionStore()
    tid, uid = uuid4(), "user-1"
    cat_id = await _seed_catalog(cat_store)
    await _seed_connection(
        oauth_store,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW - timedelta(seconds=1),
    )
    calls: list[str] = []
    pool = await _svc(oauth_store, cat_store, calls).get_or_build(tid, uid)
    assert pool.names() == []


@pytest.mark.asyncio
async def test_pending_connection_skipped() -> None:
    cat_store = InMemoryMcpConnectorCatalogStore()
    oauth_store = InMemoryMcpOAuthConnectionStore()
    tid, uid = uuid4(), "user-1"
    cat_id = await _seed_catalog(cat_store)
    await _seed_connection(
        oauth_store,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        status="pending",
    )
    calls: list[str] = []
    pool = await _svc(oauth_store, cat_store, calls).get_or_build(tid, uid)
    assert pool.names() == []


@pytest.mark.asyncio
async def test_cache_hit_no_rebuild() -> None:
    cat_store = InMemoryMcpConnectorCatalogStore()
    oauth_store = InMemoryMcpOAuthConnectionStore()
    tid, uid = uuid4(), "user-1"
    cat_id = await _seed_catalog(cat_store)
    await _seed_connection(
        oauth_store,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(hours=1),
    )
    calls: list[str] = []
    svc = _svc(oauth_store, cat_store, calls)
    await svc.get_or_build(tid, uid)
    await svc.get_or_build(tid, uid)
    assert calls == ["linear"]  # built once, second served from cache


@pytest.mark.asyncio
async def test_invalidate_rebuilds() -> None:
    cat_store = InMemoryMcpConnectorCatalogStore()
    oauth_store = InMemoryMcpOAuthConnectionStore()
    tid, uid = uuid4(), "user-1"
    cat_id = await _seed_catalog(cat_store)
    await _seed_connection(
        oauth_store,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(hours=1),
    )
    calls: list[str] = []
    svc = _svc(oauth_store, cat_store, calls)
    await svc.get_or_build(tid, uid)
    await svc.invalidate(tid, uid)
    await svc.get_or_build(tid, uid)
    assert calls == ["linear", "linear"]  # rebuilt after invalidate


@pytest.mark.asyncio
async def test_users_isolated() -> None:
    cat_store = InMemoryMcpConnectorCatalogStore()
    oauth_store = InMemoryMcpOAuthConnectionStore()
    tid = uuid4()
    cat_id = await _seed_catalog(cat_store)
    await _seed_connection(
        oauth_store,
        tenant_id=tid,
        user_id="user-1",
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(hours=1),
    )
    calls: list[str] = []
    svc = _svc(oauth_store, cat_store, calls)
    p1 = await svc.get_or_build(tid, "user-1")
    p2 = await svc.get_or_build(tid, "user-2")
    assert p1.names() == ["linear"]
    assert p2.names() == []  # user-2 has no connections
