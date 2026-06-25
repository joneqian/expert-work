"""Unit tests for :class:`InMemoryAgentInstanceStore` — Agent-Templates M1-5b."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence.agent_instance import InMemoryAgentInstanceStore

_TENANT = uuid4()


@pytest.fixture
def store() -> InMemoryAgentInstanceStore:
    return InMemoryAgentInstanceStore()


@pytest.mark.asyncio
async def test_touch_creates_then_is_idempotent(store: InMemoryAgentInstanceStore) -> None:
    user = uuid4()
    first = await store.touch(tenant_id=_TENANT, agent_code="bot", user_id=user)
    second = await store.touch(tenant_id=_TENANT, agent_code="bot", user_id=user)
    # Same binding (same id), last_active bumped.
    assert first.id == second.id
    assert second.last_active_at >= first.last_active_at
    got = await store.get(tenant_id=_TENANT, agent_code="bot", user_id=user)
    assert got is not None and got.id == first.id


@pytest.mark.asyncio
async def test_list_by_agent_lists_users(store: InMemoryAgentInstanceStore) -> None:
    u1, u2 = uuid4(), uuid4()
    await store.touch(tenant_id=_TENANT, agent_code="bot", user_id=u1)
    await store.touch(tenant_id=_TENANT, agent_code="bot", user_id=u2)
    await store.touch(tenant_id=_TENANT, agent_code="other", user_id=u1)
    rows = await store.list_by_agent(tenant_id=_TENANT, agent_code="bot")
    assert {r.user_id for r in rows} == {u1, u2}


@pytest.mark.asyncio
async def test_list_by_user_lists_agents(store: InMemoryAgentInstanceStore) -> None:
    user = uuid4()
    await store.touch(tenant_id=_TENANT, agent_code="bot", user_id=user)
    await store.touch(tenant_id=_TENANT, agent_code="helper", user_id=user)
    rows = await store.list_by_user(tenant_id=_TENANT, user_id=user)
    assert {r.agent_code for r in rows} == {"bot", "helper"}


@pytest.mark.asyncio
async def test_tenant_isolation(store: InMemoryAgentInstanceStore) -> None:
    user = uuid4()
    await store.touch(tenant_id=_TENANT, agent_code="bot", user_id=user)
    other_tenant = uuid4()
    assert await store.get(tenant_id=other_tenant, agent_code="bot", user_id=user) is None
    assert await store.list_by_agent(tenant_id=other_tenant, agent_code="bot") == []
