"""Unit tests for :class:`InMemoryPlatformSecretStore` — Stream P (P-7)."""

from __future__ import annotations

import pytest

from expert_work.persistence.platform_secrets import InMemoryPlatformSecretStore


@pytest.mark.asyncio
async def test_provider_upsert_get_list_delete() -> None:
    store = InMemoryPlatformSecretStore()
    assert await store.list_providers() == []

    created = await store.upsert_provider(
        provider="anthropic",
        secret_ref="kms://platform/anthropic",
        enabled=True,
        actor_id="admin",
    )
    assert created.provider == "anthropic"
    assert created.secret_ref == "kms://platform/anthropic"
    assert created.enabled is True
    assert created.updated_by == "admin"

    fetched = await store.get_provider("anthropic")
    assert fetched is not None
    assert fetched.secret_ref == "kms://platform/anthropic"
    assert len(await store.list_providers()) == 1

    # Upsert again preserves created_at, bumps updated_at, can disable.
    updated = await store.upsert_provider(
        provider="anthropic",
        secret_ref="secret://anthropic-rotated",
        enabled=False,
        actor_id="admin2",
    )
    assert updated.created_at == created.created_at
    assert updated.enabled is False
    assert updated.secret_ref == "secret://anthropic-rotated"

    assert await store.delete_provider("anthropic") is True
    assert await store.delete_provider("anthropic") is False
    assert await store.get_provider("anthropic") is None


@pytest.mark.asyncio
async def test_tool_upsert_get_delete_independent_of_providers() -> None:
    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="openai", secret_ref="kms://openai", enabled=True, actor_id="a"
    )
    tool = await store.upsert_tool(
        tool="web_search", secret_ref="kms://tavily", enabled=True, actor_id="a"
    )
    assert tool.tool == "web_search"
    assert len(await store.list_tools()) == 1
    # Provider and tool namespaces are independent.
    assert len(await store.list_providers()) == 1
    assert await store.delete_tool("web_search") is True
    assert await store.get_tool("web_search") is None


# ─── per-tenant overrides (Stream HX-8) ────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_provider_override_crud() -> None:
    from uuid import UUID

    tenant_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    tenant_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    store = InMemoryPlatformSecretStore()
    assert await store.list_tenant_providers() == []

    created = await store.upsert_tenant_provider(
        tenant_id=tenant_a,
        provider="anthropic",
        secret_ref="kms://tenant-a/anthropic",
        enabled=True,
        actor_id="admin",
    )
    assert created.tenant_id == tenant_a
    assert created.provider == "anthropic"

    # Upsert preserves created_at, can disable.
    updated = await store.upsert_tenant_provider(
        tenant_id=tenant_a,
        provider="anthropic",
        secret_ref="kms://tenant-a/anthropic-rotated",
        enabled=False,
        actor_id="admin2",
    )
    assert updated.created_at == created.created_at
    assert updated.enabled is False

    # Per-tenant filter vs the all-tenants cache load.
    await store.upsert_tenant_provider(
        tenant_id=tenant_b,
        provider="openai",
        secret_ref="kms://tenant-b/openai",
        enabled=True,
        actor_id="admin",
    )
    assert len(await store.list_tenant_providers()) == 2
    only_a = await store.list_tenant_providers(tenant_a)
    assert [r.tenant_id for r in only_a] == [tenant_a]

    assert await store.delete_tenant_provider(tenant_id=tenant_a, provider="anthropic") is True
    assert await store.delete_tenant_provider(tenant_id=tenant_a, provider="anthropic") is False


@pytest.mark.asyncio
async def test_tenant_tool_override_independent_namespace() -> None:
    from uuid import UUID

    tenant_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    store = InMemoryPlatformSecretStore()
    await store.upsert_tenant_tool(
        tenant_id=tenant_a,
        tool="web_search",
        secret_ref="kms://tenant-a/tavily",
        enabled=True,
        actor_id="admin",
    )
    assert len(await store.list_tenant_tools(tenant_a)) == 1
    assert await store.list_tenant_providers(tenant_a) == []
    assert await store.delete_tenant_tool(tenant_id=tenant_a, tool="web_search") is True


# ---------------------------------------------------------------------------
# Stream Y-MK — per-provider multi-key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_multiple_keys_independent_rows() -> None:
    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="deepseek",
        key_id="acct-a",
        secret_ref="kms://a",
        enabled=True,
        priority=10,
        actor_id="admin",
    )
    await store.upsert_provider(
        provider="deepseek",
        key_id="acct-b",
        secret_ref="kms://b",
        enabled=True,
        priority=20,
        actor_id="admin",
    )
    rows = await store.list_providers()
    assert len(rows) == 2  # two keys, same provider — distinct rows
    a = await store.get_provider("deepseek", "acct-a")
    assert a is not None and a.secret_ref == "kms://a" and a.priority == 10
    # default key_id is a separate slot, absent here
    assert await store.get_provider("deepseek") is None


@pytest.mark.asyncio
async def test_provider_delete_one_key_keeps_siblings() -> None:
    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="openai", key_id="k1", secret_ref="kms://k1", enabled=True, actor_id="a"
    )
    await store.upsert_provider(
        provider="openai", key_id="k2", secret_ref="kms://k2", enabled=True, actor_id="a"
    )
    assert await store.delete_provider("openai", "k1") is True
    assert await store.delete_provider("openai", "k1") is False  # already gone
    remaining = await store.list_providers()
    assert [r.key_id for r in remaining] == ["k2"]


@pytest.mark.asyncio
async def test_provider_upsert_same_key_updates_in_place() -> None:
    store = InMemoryPlatformSecretStore()
    await store.upsert_provider(
        provider="openai",
        key_id="k1",
        secret_ref="kms://old",
        enabled=True,
        priority=100,
        actor_id="a",
    )
    await store.upsert_provider(
        provider="openai",
        key_id="k1",
        secret_ref="kms://new",
        enabled=False,
        priority=5,
        actor_id="b",
    )
    rows = await store.list_providers()
    assert len(rows) == 1
    assert rows[0].secret_ref == "kms://new"
    assert rows[0].enabled is False
    assert rows[0].priority == 5
