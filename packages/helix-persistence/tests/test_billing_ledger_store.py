"""Unit tests for :class:`InMemoryTenantBillingLedgerStore` — Stream Y4."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from helix_agent.persistence.billing.ledger import InMemoryTenantBillingLedgerStore
from helix_agent.protocol import TenantBillingLedgerRecord


def _record(**over: object) -> TenantBillingLedgerRecord:
    now = datetime.now(UTC)
    kwargs: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "month": date(2026, 6, 1),
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "agent_name": "support",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "base_cost_micros": 1000,
        "markup_cost_micros": 200,
        "billed_cost_micros": 1200,
        "priced": True,
        "rate_card_priced_at": now,
        "created_at": now,
        "updated_at": now,
    }
    kwargs.update(over)
    return TenantBillingLedgerRecord(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def store() -> InMemoryTenantBillingLedgerStore:
    return InMemoryTenantBillingLedgerStore()


@pytest.mark.asyncio
async def test_upsert_insert_then_overwrite_single_row(
    store: InMemoryTenantBillingLedgerStore,
) -> None:
    tenant = uuid4()
    first = _record(tenant_id=tenant, billed_cost_micros=1200, base_cost_micros=1000)
    first_stored = await store.upsert(first)
    # Same bucket key, different values → overwrite, not a second row.
    second = _record(
        tenant_id=tenant,
        base_cost_micros=2000,
        markup_cost_micros=400,
        billed_cost_micros=2400,
        input_tokens=200,
    )
    stored = await store.upsert(second)

    rows = await store.list_for_tenant(tenant_id=tenant, month=date(2026, 6, 1))
    assert len(rows) == 1
    assert rows[0].billed_cost_micros == 2400
    assert rows[0].input_tokens == 200
    # id + created_at stay stable across the overwrite (row identity).
    assert stored.id == first_stored.id
    assert stored.created_at == first_stored.created_at


@pytest.mark.asyncio
async def test_distinct_buckets_coexist(store: InMemoryTenantBillingLedgerStore) -> None:
    tenant = uuid4()
    await store.upsert(_record(tenant_id=tenant, agent_name="support"))
    await store.upsert(_record(tenant_id=tenant, agent_name="sales"))
    await store.upsert(_record(tenant_id=tenant, model="claude-sonnet-4-6"))
    rows = await store.list_for_tenant(tenant_id=tenant, month=date(2026, 6, 1))
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_list_filters_by_month_and_tenant(
    store: InMemoryTenantBillingLedgerStore,
) -> None:
    tenant = uuid4()
    other = uuid4()
    await store.upsert(_record(tenant_id=tenant, month=date(2026, 6, 1)))
    await store.upsert(_record(tenant_id=tenant, month=date(2026, 7, 1)))
    await store.upsert(_record(tenant_id=other, month=date(2026, 6, 1)))
    rows = await store.list_for_tenant(tenant_id=tenant, month=date(2026, 6, 1))
    assert len(rows) == 1
    assert rows[0].tenant_id == tenant
    assert rows[0].month == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_delete_month(store: InMemoryTenantBillingLedgerStore) -> None:
    tenant = uuid4()
    await store.upsert(_record(tenant_id=tenant, agent_name="a", month=date(2026, 6, 1)))
    await store.upsert(_record(tenant_id=tenant, agent_name="b", month=date(2026, 6, 1)))
    await store.upsert(_record(tenant_id=tenant, agent_name="c", month=date(2026, 7, 1)))
    deleted = await store.delete_month(tenant_id=tenant, month=date(2026, 6, 1))
    assert deleted == 2
    remaining = await store.list_for_tenant(tenant_id=tenant, month=date(2026, 7, 1))
    assert len(remaining) == 1
