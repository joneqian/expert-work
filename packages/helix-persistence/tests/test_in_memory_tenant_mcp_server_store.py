"""Unit tests for the in-memory tenant MCP server store."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence.tenant_mcp_server import (
    InMemoryTenantMcpServerStore,
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
)
from helix_agent.protocol import TenantMcpServerPatch, TenantMcpServerRecord


async def _make(
    store: InMemoryTenantMcpServerStore, tenant_id: UUID, **over: Any
) -> TenantMcpServerRecord:
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "name": "github",
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "auth_type": "none",
        "token_secret_ref": None,
        "timeout_s": 30.0,
        "created_by": "admin@acme",
    }
    kwargs.update(over)
    return await store.create(**kwargs)


@pytest.mark.asyncio
async def test_create_then_get_round_trip() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    created = await _make(store, tid)
    assert created.name == "github"
    assert created.tenant_id == tid
    fetched = await store.get(tenant_id=tid, name="github")
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_absent_returns_none() -> None:
    store = InMemoryTenantMcpServerStore()
    assert await store.get(tenant_id=uuid4(), name="nope") is None


@pytest.mark.asyncio
async def test_duplicate_name_same_tenant_rejected() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _make(store, tid)
    with pytest.raises(TenantMcpServerAlreadyExistsError):
        await _make(store, tid)


@pytest.mark.asyncio
async def test_same_name_different_tenant_ok() -> None:
    store = InMemoryTenantMcpServerStore()
    a, b = uuid4(), uuid4()
    await _make(store, a)
    await _make(store, b)  # no conflict
    assert (await store.get(tenant_id=a, name="github")) is not None
    assert (await store.get(tenant_id=b, name="github")) is not None


@pytest.mark.asyncio
async def test_list_for_tenant_sorted_and_scoped() -> None:
    store = InMemoryTenantMcpServerStore()
    a, b = uuid4(), uuid4()
    await _make(store, a, name="zeta")
    await _make(store, a, name="alpha")
    await _make(store, b, name="gamma")
    names = [r.name for r in await store.list_for_tenant(tenant_id=a)]
    assert names == ["alpha", "zeta"]


@pytest.mark.asyncio
async def test_update_applies_partial_fields() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _make(store, tid)
    original = await store.get(tenant_id=tid, name="github")
    assert original is not None
    updated = await store.update(
        tenant_id=tid,
        name="github",
        patch=TenantMcpServerPatch(url="https://new.example.com/mcp", enabled=False),
    )
    assert updated.url == "https://new.example.com/mcp"
    assert updated.enabled is False
    assert updated.updated_at >= updated.created_at
    assert updated.timeout_s == original.timeout_s
    assert updated.transport == original.transport
    assert updated.auth_type == original.auth_type
    assert updated.id == original.id
    assert updated.created_at == original.created_at


@pytest.mark.asyncio
async def test_update_absent_raises() -> None:
    store = InMemoryTenantMcpServerStore()
    with pytest.raises(TenantMcpServerNotFoundError):
        await store.update(
            tenant_id=uuid4(), name="nope", patch=TenantMcpServerPatch(enabled=False)
        )


@pytest.mark.asyncio
async def test_delete_removes_row() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _make(store, tid)
    await store.delete(tenant_id=tid, name="github")
    assert await store.get(tenant_id=tid, name="github") is None


@pytest.mark.asyncio
async def test_delete_absent_raises() -> None:
    store = InMemoryTenantMcpServerStore()
    with pytest.raises(TenantMcpServerNotFoundError):
        await store.delete(tenant_id=uuid4(), name="nope")


@pytest.mark.asyncio
async def test_list_for_tenant_empty_when_no_rows() -> None:
    store = InMemoryTenantMcpServerStore()
    assert await store.list_for_tenant(tenant_id=uuid4()) == []


@pytest.mark.asyncio
async def test_create_without_catalog_id_defaults_none() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    created = await _make(store, tid)
    assert created.catalog_id is None


@pytest.mark.asyncio
async def test_create_with_catalog_id_round_trips() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    catalog_id = uuid4()
    created = await _make(store, tid, catalog_id=catalog_id)
    assert created.catalog_id == catalog_id
    fetched = await store.get(tenant_id=tid, name="github")
    assert fetched is not None and fetched.catalog_id == catalog_id
