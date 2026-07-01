"""Unit tests for :class:`InMemoryTokenUsageStore` — Stream G.9."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

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
async def test_provider_round_trips(store: InMemoryTokenUsageStore) -> None:
    # Stream Y-3 — additive provider column round-trips; legacy None default holds.
    tenant = uuid4()
    with_provider = await store.insert(
        TokenUsageRecord(
            tenant_id=tenant,
            agent_name="bot",
            agent_version="1.0.0",
            model="claude-opus-4-8",
            provider="anthropic",
            input_tokens=10,
            output_tokens=5,
        )
    )
    assert with_provider.provider == "anthropic"
    legacy = await store.insert(
        TokenUsageRecord(
            tenant_id=tenant,
            agent_name="bot",
            agent_version="1.0.0",
            model="claude-opus-4-8",
            input_tokens=10,
            output_tokens=5,
        )
    )
    assert legacy.provider is None


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


# ---------------------------------------------------------------------------
# list_for_tenant_window — Stream Y4 (half-open [start, end))
# ---------------------------------------------------------------------------


async def _insert_at(
    store: InMemoryTokenUsageStore, *, tenant_id: UUID, observed_at: datetime
) -> None:
    """Insert a row, then pin its observed_at (insert() stamps now())."""
    stored = await store.insert(
        TokenUsageRecord(
            tenant_id=tenant_id,
            agent_name="bot",
            agent_version="1.0.0",
            model="claude-sonnet-4-6",
            input_tokens=1,
        )
    )
    # Replace the just-inserted row with a fixed observed_at.
    store._rows[-1] = replace(stored, observed_at=observed_at)


@pytest.mark.asyncio
async def test_window_is_half_open(store: InMemoryTokenUsageStore) -> None:
    tenant = uuid4()
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = datetime(2026, 7, 1, tzinfo=UTC)
    # observed_at == start  → included
    await _insert_at(store, tenant_id=tenant, observed_at=start)
    # mid-window            → included
    await _insert_at(store, tenant_id=tenant, observed_at=datetime(2026, 6, 15, tzinfo=UTC))
    # observed_at == end    → excluded
    await _insert_at(store, tenant_id=tenant, observed_at=end)
    # before start          → excluded
    await _insert_at(store, tenant_id=tenant, observed_at=datetime(2026, 5, 31, tzinfo=UTC))

    rows = list(await store.list_for_tenant_window(tenant_id=tenant, start=start, end=end))
    observed = sorted(r.observed_at for r in rows if r.observed_at is not None)
    assert observed == [start, datetime(2026, 6, 15, tzinfo=UTC)]


@pytest.mark.asyncio
async def test_window_isolates_tenants(store: InMemoryTokenUsageStore) -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = datetime(2026, 7, 1, tzinfo=UTC)
    await _insert_at(store, tenant_id=tenant_a, observed_at=start)
    await _insert_at(store, tenant_id=tenant_b, observed_at=start)
    rows = list(await store.list_for_tenant_window(tenant_id=tenant_a, start=start, end=end))
    assert len(rows) == 1
    assert rows[0].tenant_id == tenant_a


# ---------------------------------------------------------------------------
# totals_by_trace_ids — Runs enrichment (per-run token summary, joined by
# trace_id since token_usage has no run_id column)
# ---------------------------------------------------------------------------


async def _usage(
    store: InMemoryTokenUsageStore,
    *,
    tenant_id: UUID,
    trace_id: str | None,
    model: str,
    inp: int,
    out: int,
    cache_read: int = 0,
) -> None:
    await store.insert(
        TokenUsageRecord(
            tenant_id=tenant_id,
            agent_name="bot",
            agent_version="1.0.0",
            model=model,
            trace_id=trace_id,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cache_read,
        )
    )


@pytest.mark.asyncio
async def test_totals_group_and_sum_by_trace(store: InMemoryTokenUsageStore) -> None:
    tenant = uuid4()
    # trace A — two LLM calls, two models.
    await _usage(store, tenant_id=tenant, trace_id="aaaa", model="m1", inp=10, out=5, cache_read=2)
    await _usage(store, tenant_id=tenant, trace_id="aaaa", model="m2", inp=20, out=7)
    # trace B — one call.
    await _usage(store, tenant_id=tenant, trace_id="bbbb", model="m1", inp=3, out=1)

    totals = await store.totals_by_trace_ids(["aaaa", "bbbb"])
    a = totals["aaaa"]
    assert a.input_tokens == 30
    assert a.output_tokens == 12
    assert a.cache_read_tokens == 2
    assert a.total_tokens == 42
    assert a.llm_calls == 2
    assert a.models == ("m1", "m2")  # distinct, sorted
    assert totals["bbbb"].llm_calls == 1


@pytest.mark.asyncio
async def test_totals_skips_null_trace_and_unrequested_ids(
    store: InMemoryTokenUsageStore,
) -> None:
    tenant = uuid4()
    await _usage(store, tenant_id=tenant, trace_id=None, model="m1", inp=99, out=99)  # legacy row
    await _usage(store, tenant_id=tenant, trace_id="cccc", model="m1", inp=1, out=1)

    totals = await store.totals_by_trace_ids(["cccc", "missing"])
    # Null-trace row ignored; a requested id with no usage is simply absent.
    assert set(totals) == {"cccc"}


@pytest.mark.asyncio
async def test_totals_empty_input_returns_empty(store: InMemoryTokenUsageStore) -> None:
    assert await store.totals_by_trace_ids([]) == {}
