"""In-memory ``McpOAuthConnectionStore`` tests — Stream MCP-OAUTH (OA-1b)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from expert_work.persistence.mcp_oauth_connection import (
    InMemoryMcpOAuthConnectionStore,
    McpOAuthConnectionAlreadyExistsError,
    McpOAuthConnectionNotFoundError,
)
from expert_work.protocol import McpOAuthConnectionPatch

_ACCESS = "secret://expert-work/tenant/t/user/u/mcp/linear/access"


@pytest.mark.asyncio
async def test_create_pending_then_get() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    rec = await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
        oauth_state="state-abc",
        pkce_verifier="verifier-xyz",
    )
    assert rec.status == "pending"
    got = await store.get(connection_id=rec.id, tenant_id=tid, user_id="u1")
    assert got is not None and got.oauth_state == "state-abc"


@pytest.mark.asyncio
async def test_duplicate_connector_rejected() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
    )
    with pytest.raises(McpOAuthConnectionAlreadyExistsError):
        await store.create(
            tenant_id=tid,
            user_id="u1",
            catalog_id=cat,
            name="linear",
            resolved_url="https://mcp.linear.app/sse",
        )


@pytest.mark.asyncio
async def test_same_connector_different_user_allowed() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    a = await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
    )
    b = await store.create(
        tenant_id=tid,
        user_id="u2",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
    )
    assert a.id != b.id


@pytest.mark.asyncio
async def test_user_isolation_on_get() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    rec = await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
    )
    # user u2 cannot read u1's connection
    assert await store.get(connection_id=rec.id, tenant_id=tid, user_id="u2") is None


@pytest.mark.asyncio
async def test_get_by_state_and_for_connector() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    rec = await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
        oauth_state="st-1",
    )
    by_state = await store.get_by_state(tenant_id=tid, user_id="u1", oauth_state="st-1")
    assert by_state is not None and by_state.id == rec.id
    by_conn = await store.get_for_connector(tenant_id=tid, user_id="u1", catalog_id=cat)
    assert by_conn is not None and by_conn.id == rec.id


@pytest.mark.asyncio
async def test_update_connect_clears_flow_state() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    rec = await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
        oauth_state="st",
        pkce_verifier="pv",
    )
    updated = await store.update(
        connection_id=rec.id,
        tenant_id=tid,
        user_id="u1",
        patch=McpOAuthConnectionPatch(
            status="connected", access_token_ref=_ACCESS, scopes="read", clear_flow_state=True
        ),
    )
    assert updated.status == "connected"
    assert updated.access_token_ref == _ACCESS
    assert updated.oauth_state is None
    assert updated.pkce_verifier is None


@pytest.mark.asyncio
async def test_list_for_user_sorted() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid = uuid4()
    await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=uuid4(),
        name="zeta",
        resolved_url="https://z/sse",
    )
    await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=uuid4(),
        name="alpha",
        resolved_url="https://a/sse",
    )
    names = [r.name for r in await store.list_for_user(tenant_id=tid, user_id="u1")]
    assert names == ["alpha", "zeta"]


@pytest.mark.asyncio
async def test_delete_and_absent_raises() -> None:
    store = InMemoryMcpOAuthConnectionStore()
    tid, cat = uuid4(), uuid4()
    rec = await store.create(
        tenant_id=tid,
        user_id="u1",
        catalog_id=cat,
        name="linear",
        resolved_url="https://mcp.linear.app/sse",
    )
    await store.delete(connection_id=rec.id, tenant_id=tid, user_id="u1")
    assert await store.get(connection_id=rec.id, tenant_id=tid, user_id="u1") is None
    with pytest.raises(McpOAuthConnectionNotFoundError):
        await store.delete(connection_id=rec.id, tenant_id=tid, user_id="u1")
