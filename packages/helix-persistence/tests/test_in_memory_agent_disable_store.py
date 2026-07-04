"""Unit tests for :class:`InMemoryAgentDisableStore` — Stream RT-4 (RT-ADR-16)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryAgentDisableStore


@pytest.mark.asyncio
async def test_get_missing_row_returns_none() -> None:
    store = InMemoryAgentDisableStore()
    assert await store.get(tenant_id=uuid4(), agent_name="support-bot") is None


@pytest.mark.asyncio
async def test_set_disabled_then_get_round_trips() -> None:
    store = InMemoryAgentDisableStore()
    tenant = uuid4()
    rec = await store.set_disabled(
        tenant_id=tenant,
        agent_name="support-bot",
        disabled=True,
        reason="incident-42",
        disabled_by="admin@acme",
    )
    assert rec.tenant_id == tenant
    assert rec.agent_name == "support-bot"
    assert rec.disabled is True
    assert rec.reason == "incident-42"
    assert rec.disabled_by == "admin@acme"
    assert rec.disabled_at is not None

    fetched = await store.get(tenant_id=tenant, agent_name="support-bot")
    assert fetched is not None
    assert fetched.disabled is True
    assert fetched.reason == "incident-42"


@pytest.mark.asyncio
async def test_set_disabled_is_upsert() -> None:
    store = InMemoryAgentDisableStore()
    tenant = uuid4()
    await store.set_disabled(
        tenant_id=tenant, agent_name="a", disabled=True, reason="first", disabled_by="u1"
    )
    await store.set_disabled(
        tenant_id=tenant, agent_name="a", disabled=True, reason="second", disabled_by="u2"
    )
    fetched = await store.get(tenant_id=tenant, agent_name="a")
    assert fetched is not None
    assert fetched.reason == "second"
    assert fetched.disabled_by == "u2"


@pytest.mark.asyncio
async def test_enable_clears_disable_metadata() -> None:
    store = InMemoryAgentDisableStore()
    tenant = uuid4()
    await store.set_disabled(
        tenant_id=tenant, agent_name="a", disabled=True, reason="incident", disabled_by="admin"
    )
    enabled = await store.set_disabled(
        tenant_id=tenant, agent_name="a", disabled=False, reason=None, disabled_by="admin"
    )
    assert enabled.disabled is False
    assert enabled.reason is None
    assert enabled.disabled_by is None
    assert enabled.disabled_at is None


@pytest.mark.asyncio
async def test_rows_are_scoped_by_tenant_and_name() -> None:
    store = InMemoryAgentDisableStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    await store.set_disabled(
        tenant_id=tenant_a, agent_name="a", disabled=True, reason=None, disabled_by="x"
    )
    # Same name, different tenant → independent row (still absent).
    assert await store.get(tenant_id=tenant_b, agent_name="a") is None
    # Same tenant, different name → independent row.
    assert await store.get(tenant_id=tenant_a, agent_name="other") is None
