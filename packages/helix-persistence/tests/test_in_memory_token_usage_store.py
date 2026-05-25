"""Unit tests for :class:`InMemoryTokenUsageStore` — Stream G.9."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence.token_usage_store import (
    InMemoryTokenUsageStore,
    TokenUsageRecord,
)


@pytest.fixture
def store() -> InMemoryTokenUsageStore:
    return InMemoryTokenUsageStore()


@pytest.mark.asyncio
async def test_insert_fills_id_and_observed_at(store: InMemoryTokenUsageStore) -> None:
    tenant = uuid4()
    stored = await store.insert(
        TokenUsageRecord(
            tenant_id=tenant,
            agent_name="customer-support-bot",
            agent_version="3.4.2",
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=512,
        )
    )
    assert stored.id is not None
    assert stored.observed_at is not None
    assert stored.input_tokens == 1000
    assert stored.cache_read_tokens == 512


@pytest.mark.asyncio
async def test_list_for_tenant_newest_first(store: InMemoryTokenUsageStore) -> None:
    tenant = uuid4()
    for i in range(3):
        await store.insert(
            TokenUsageRecord(
                tenant_id=tenant,
                agent_name="bot",
                agent_version="1.0.0",
                model="claude-sonnet-4-6",
                input_tokens=i,
                output_tokens=i,
            )
        )
    rows = list(await store.list_for_tenant(tenant_id=tenant))
    assert len(rows) == 3
    assert rows[0].input_tokens == 2
    assert rows[2].input_tokens == 0


@pytest.mark.asyncio
async def test_list_for_tenant_filters_by_agent_and_model(
    store: InMemoryTokenUsageStore,
) -> None:
    tenant = uuid4()
    await store.insert(
        TokenUsageRecord(
            tenant_id=tenant,
            agent_name="a",
            agent_version="1.0.0",
            model="claude-sonnet-4-6",
        )
    )
    await store.insert(
        TokenUsageRecord(
            tenant_id=tenant,
            agent_name="b",
            agent_version="1.0.0",
            model="claude-sonnet-4-6",
        )
    )
    await store.insert(
        TokenUsageRecord(
            tenant_id=tenant,
            agent_name="a",
            agent_version="1.0.0",
            model="gpt-4o",
        )
    )
    only_a = list(await store.list_for_tenant(tenant_id=tenant, agent_name="a"))
    assert {r.model for r in only_a} == {"claude-sonnet-4-6", "gpt-4o"}
    a_sonnet = list(
        await store.list_for_tenant(tenant_id=tenant, agent_name="a", model="claude-sonnet-4-6")
    )
    assert len(a_sonnet) == 1


@pytest.mark.asyncio
async def test_list_does_not_leak_across_tenants(
    store: InMemoryTokenUsageStore,
) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    await store.insert(
        TokenUsageRecord(
            tenant_id=tenant_a,
            agent_name="a",
            agent_version="1.0.0",
            model="m",
        )
    )
    await store.insert(
        TokenUsageRecord(
            tenant_id=tenant_b,
            agent_name="a",
            agent_version="1.0.0",
            model="m",
        )
    )
    rows = list(await store.list_for_tenant(tenant_id=tenant_a))
    assert len(rows) == 1
    assert rows[0].tenant_id == tenant_a
