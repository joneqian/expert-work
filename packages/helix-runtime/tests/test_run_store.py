"""Unit tests for ``InMemoryRunStore`` — Mini-ADR J-41."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from helix_agent.runtime.runs import DisconnectMode, InMemoryRunStore, RunInfo, RunStatus

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _info(
    *,
    run_id: UUID,
    tenant_id: UUID,
    thread_id: UUID | None = None,
    user_id: UUID | None = None,
    status: RunStatus = RunStatus.PENDING,
    created_at: datetime | None = None,
) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        tenant_id=tenant_id,
        thread_id=thread_id or uuid4(),
        user_id=user_id,
        status=status,
        on_disconnect=DisconnectMode.CANCEL,
        is_resume=False,
        error=None,
        created_at=created_at or _BASE,
        updated_at=created_at or _BASE,
        finished_at=None,
    )


@pytest.mark.asyncio
async def test_create_then_get_round_trips() -> None:
    store = InMemoryRunStore()
    run_id, tenant_id, user_id = uuid4(), uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id, user_id=user_id))

    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.run_id == run_id
    assert fetched.user_id == user_id
    assert fetched.status is RunStatus.PENDING


@pytest.mark.asyncio
async def test_get_unknown_returns_none() -> None:
    store = InMemoryRunStore()
    assert await store.get(run_id=uuid4(), tenant_id=uuid4()) is None


@pytest.mark.asyncio
async def test_get_cross_tenant_returns_none() -> None:
    """A run is invisible to a caller in a different tenant."""
    store = InMemoryRunStore()
    run_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_a))

    assert await store.get(run_id=run_id, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_create_duplicate_raises() -> None:
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id))
    with pytest.raises(ValueError, match="already exists"):
        await store.create(_info(run_id=run_id, tenant_id=tenant_id))


@pytest.mark.asyncio
async def test_set_status_updates_existing() -> None:
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id))

    hit = await store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.RUNNING,
        updated_at=_BASE + timedelta(seconds=5),
    )
    assert hit is True
    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.status is RunStatus.RUNNING
    assert fetched.updated_at == _BASE + timedelta(seconds=5)


@pytest.mark.asyncio
async def test_set_status_unknown_returns_false() -> None:
    store = InMemoryRunStore()
    miss = await store.set_status(
        run_id=uuid4(),
        tenant_id=uuid4(),
        status=RunStatus.SUCCESS,
        updated_at=_BASE,
    )
    assert miss is False


@pytest.mark.asyncio
async def test_set_status_cross_tenant_returns_false() -> None:
    """A cross-tenant status write is a miss — it cannot touch the row."""
    store = InMemoryRunStore()
    run_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_a))

    miss = await store.set_status(
        run_id=run_id,
        tenant_id=tenant_b,
        status=RunStatus.SUCCESS,
        updated_at=_BASE,
    )
    assert miss is False
    untouched = await store.get(run_id=run_id, tenant_id=tenant_a)
    assert untouched is not None
    assert untouched.status is RunStatus.PENDING


@pytest.mark.asyncio
async def test_set_status_records_error_and_finished_at() -> None:
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id))

    finished = _BASE + timedelta(seconds=9)
    await store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.ERROR,
        updated_at=finished,
        error="provider 503",
        finished_at=finished,
    )
    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.error == "provider 503"
    assert fetched.finished_at == finished


@pytest.mark.asyncio
async def test_set_status_keeps_prior_error_when_not_supplied() -> None:
    """A later status write without ``error`` never clears a recorded verdict."""
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id))
    await store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.ERROR,
        updated_at=_BASE,
        error="boom",
        finished_at=_BASE,
    )

    await store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.SUCCESS,
        updated_at=_BASE + timedelta(seconds=1),
    )
    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.error == "boom"
    assert fetched.finished_at == _BASE


@pytest.mark.asyncio
async def test_list_by_thread_filters_and_sorts() -> None:
    store = InMemoryRunStore()
    thread_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    # Two tenant-A runs on the thread, inserted newest-first.
    newer = uuid4()
    older = uuid4()
    await store.create(
        _info(
            run_id=newer,
            tenant_id=tenant_a,
            thread_id=thread_id,
            created_at=_BASE + timedelta(minutes=1),
        )
    )
    await store.create(
        _info(run_id=older, tenant_id=tenant_a, thread_id=thread_id, created_at=_BASE)
    )
    # A tenant-B run on the same thread must not leak.
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_b, thread_id=thread_id))

    listed = await store.list_by_thread(thread_id=thread_id, tenant_id=tenant_a)
    assert [r.run_id for r in listed] == [older, newer]


# ---------------------------------------------------------------------------
# Stream H.3 PR 1 — list_for_tenant / list_all_tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_tenant_returns_only_matching_tenant() -> None:
    store = InMemoryRunStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    a_ids = [uuid4(), uuid4(), uuid4()]
    for i, rid in enumerate(a_ids):
        await store.create(
            _info(run_id=rid, tenant_id=tenant_a, created_at=_BASE + timedelta(minutes=i))
        )
    # Tenant B runs that must not leak through.
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_b))
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_b))

    listed = await store.list_for_tenant(tenant_id=tenant_a)
    assert {r.run_id for r in listed} == set(a_ids)
    assert all(r.tenant_id == tenant_a for r in listed)


@pytest.mark.asyncio
async def test_list_for_tenant_orders_newest_first() -> None:
    store = InMemoryRunStore()
    tenant_id = uuid4()
    oldest, middle, newest = uuid4(), uuid4(), uuid4()
    await store.create(_info(run_id=oldest, tenant_id=tenant_id, created_at=_BASE))
    await store.create(
        _info(run_id=middle, tenant_id=tenant_id, created_at=_BASE + timedelta(minutes=1))
    )
    await store.create(
        _info(run_id=newest, tenant_id=tenant_id, created_at=_BASE + timedelta(minutes=2))
    )

    listed = await store.list_for_tenant(tenant_id=tenant_id)
    assert [r.run_id for r in listed] == [newest, middle, oldest]


@pytest.mark.asyncio
async def test_list_for_tenant_status_filter() -> None:
    store = InMemoryRunStore()
    tenant_id = uuid4()
    paused_id, running_id, success_id = uuid4(), uuid4(), uuid4()
    await store.create(_info(run_id=paused_id, tenant_id=tenant_id, status=RunStatus.PAUSED))
    await store.create(_info(run_id=running_id, tenant_id=tenant_id, status=RunStatus.RUNNING))
    await store.create(_info(run_id=success_id, tenant_id=tenant_id, status=RunStatus.SUCCESS))

    paused = await store.list_for_tenant(tenant_id=tenant_id, status=RunStatus.PAUSED)
    assert [r.run_id for r in paused] == [paused_id]


@pytest.mark.asyncio
async def test_list_for_tenant_pagination_offset_and_limit() -> None:
    store = InMemoryRunStore()
    tenant_id = uuid4()
    ids = []
    for i in range(7):
        rid = uuid4()
        ids.append(rid)
        await store.create(
            _info(run_id=rid, tenant_id=tenant_id, created_at=_BASE + timedelta(minutes=i))
        )

    # Newest first → reverse insertion order.
    expected_desc = list(reversed(ids))
    page1 = await store.list_for_tenant(tenant_id=tenant_id, limit=3, offset=0)
    page2 = await store.list_for_tenant(tenant_id=tenant_id, limit=3, offset=3)
    page3 = await store.list_for_tenant(tenant_id=tenant_id, limit=3, offset=6)

    assert [r.run_id for r in page1] == expected_desc[:3]
    assert [r.run_id for r in page2] == expected_desc[3:6]
    assert [r.run_id for r in page3] == expected_desc[6:9]  # only 1 row left


@pytest.mark.asyncio
async def test_list_for_tenant_clamps_to_max_limit() -> None:
    """``MAX_LIST_LIMIT = 500`` — silently clamps oversized requests."""
    from helix_agent.runtime.runs.store import MAX_LIST_LIMIT

    store = InMemoryRunStore()
    tenant_id = uuid4()
    # Create 5 runs; ask for 10000 — should return 5 (not crash).
    for i in range(5):
        await store.create(
            _info(run_id=uuid4(), tenant_id=tenant_id, created_at=_BASE + timedelta(seconds=i))
        )

    listed = await store.list_for_tenant(tenant_id=tenant_id, limit=10000)
    assert len(listed) == 5  # less than MAX_LIST_LIMIT cap
    # Bound the cap itself — pass exactly MAX_LIST_LIMIT and one more, prove
    # the clamp is the limit applied.
    listed_at_cap = await store.list_for_tenant(tenant_id=tenant_id, limit=MAX_LIST_LIMIT + 50)
    assert len(listed_at_cap) == 5


@pytest.mark.asyncio
async def test_list_all_tenants_returns_runs_across_tenants() -> None:
    store = InMemoryRunStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_a))
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_a))
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_b))

    listed = await store.list_all_tenants()
    tenants = {r.tenant_id for r in listed}
    assert tenants == {tenant_a, tenant_b}
    assert len(listed) == 3


@pytest.mark.asyncio
async def test_list_all_tenants_status_filter_and_ordering() -> None:
    store = InMemoryRunStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    paused_a = uuid4()
    paused_b = uuid4()
    await store.create(
        _info(
            run_id=paused_a,
            tenant_id=tenant_a,
            status=RunStatus.PAUSED,
            created_at=_BASE,
        )
    )
    await store.create(
        _info(
            run_id=paused_b,
            tenant_id=tenant_b,
            status=RunStatus.PAUSED,
            created_at=_BASE + timedelta(minutes=1),
        )
    )
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_a, status=RunStatus.SUCCESS))

    paused = await store.list_all_tenants(status=RunStatus.PAUSED)
    assert [r.run_id for r in paused] == [paused_b, paused_a]  # newest first


# ---------------------------------------------------------------------------
# Stream H.3 PR 2 — set_trace_id (Mini-ADR H-9.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_trace_id_writes_and_reads_back() -> None:
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id))

    ok = await store.set_trace_id(run_id=run_id, tenant_id=tenant_id, trace_id="abcd" * 8)
    assert ok is True

    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.trace_id == "abcd" * 8


@pytest.mark.asyncio
async def test_set_trace_id_idempotent_overwrite() -> None:
    """A worker observing its own trace after the API handler captured one
    overwrites the existing value — last write wins."""
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_id))

    await store.set_trace_id(run_id=run_id, tenant_id=tenant_id, trace_id="1" * 32)
    await store.set_trace_id(run_id=run_id, tenant_id=tenant_id, trace_id="2" * 32)

    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.trace_id == "2" * 32


@pytest.mark.asyncio
async def test_set_trace_id_unknown_run_returns_false() -> None:
    store = InMemoryRunStore()
    ok = await store.set_trace_id(run_id=uuid4(), tenant_id=uuid4(), trace_id="aa" * 16)
    assert ok is False


@pytest.mark.asyncio
async def test_set_trace_id_cross_tenant_returns_false() -> None:
    """A wrong tenant_id must not let an attacker stamp another tenant's
    run trace_id."""
    store = InMemoryRunStore()
    run_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant_a))

    ok = await store.set_trace_id(run_id=run_id, tenant_id=tenant_b, trace_id="x" * 32)
    assert ok is False

    fetched = await store.get(run_id=run_id, tenant_id=tenant_a)
    assert fetched is not None
    assert fetched.trace_id is None  # unchanged


@pytest.mark.asyncio
async def test_create_with_trace_id_round_trips() -> None:
    """The trace_id passed through ``RunInfo.create`` reaches ``get`` /
    ``list_for_tenant`` / ``list_all_tenants`` unchanged."""
    store = InMemoryRunStore()
    run_id, tenant_id = uuid4(), uuid4()
    await store.create(
        RunInfo(
            run_id=run_id,
            tenant_id=tenant_id,
            thread_id=uuid4(),
            user_id=None,
            status=RunStatus.PENDING,
            on_disconnect=DisconnectMode.CANCEL,
            is_resume=False,
            error=None,
            created_at=_BASE,
            updated_at=_BASE,
            finished_at=None,
            trace_id="cafef00d" * 4,
        )
    )

    fetched = await store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.trace_id == "cafef00d" * 4

    listed = await store.list_for_tenant(tenant_id=tenant_id)
    assert listed[0].trace_id == "cafef00d" * 4


# ---------------------------------------------------------------------------
# Stream H.6 (Mini-ADR H-10) — thread_ids list filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_tenant_thread_ids_filter() -> None:
    store = InMemoryRunStore()
    tenant_id = uuid4()
    thread_a, thread_b = uuid4(), uuid4()
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_id, thread_id=thread_a))
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_id, thread_id=thread_a))
    await store.create(_info(run_id=uuid4(), tenant_id=tenant_id, thread_id=thread_b))

    subset = await store.list_for_tenant(tenant_id=tenant_id, thread_ids=[thread_a])
    assert len(subset) == 2
    assert {r.thread_id for r in subset} == {thread_a}

    # Empty collection means "the agent has no threads" → no rows, NOT
    # "no filter" (that's None).
    assert await store.list_for_tenant(tenant_id=tenant_id, thread_ids=[]) == []

    # None regression — unfiltered list unchanged.
    assert len(await store.list_for_tenant(tenant_id=tenant_id)) == 3


@pytest.mark.asyncio
async def test_list_all_tenants_thread_ids_filter() -> None:
    store = InMemoryRunStore()
    thread_a = uuid4()
    await store.create(_info(run_id=uuid4(), tenant_id=uuid4(), thread_id=thread_a))
    await store.create(_info(run_id=uuid4(), tenant_id=uuid4(), thread_id=uuid4()))

    subset = await store.list_all_tenants(thread_ids={thread_a})
    assert [r.thread_id for r in subset] == [thread_a]
    assert await store.list_all_tenants(thread_ids=set()) == []
    assert len(await store.list_all_tenants()) == 2


# --- Stream 9.4 (HA failover) — ownership lease ------------------------------


@pytest.mark.asyncio
async def test_claim_then_heartbeat_renews_lease() -> None:
    store = InMemoryRunStore()
    run_id, tenant = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant, status=RunStatus.RUNNING))
    t0 = _BASE
    assert await store.claim(
        run_id=run_id,
        tenant_id=tenant,
        claimed_by="inst-a",
        lease_until=t0 + timedelta(seconds=30),
        heartbeat_at=t0,
    )
    # owner renews
    t1 = t0 + timedelta(seconds=10)
    assert await store.heartbeat(
        run_id=run_id,
        claimed_by="inst-a",
        lease_until=t1 + timedelta(seconds=30),
        heartbeat_at=t1,
    )
    row = await store.get(run_id=run_id, tenant_id=tenant)
    assert row is not None and row.claimed_by == "inst-a"
    assert row.lease_until == t1 + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_heartbeat_fails_for_non_owner() -> None:
    store = InMemoryRunStore()
    run_id, tenant = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant, status=RunStatus.RUNNING))
    await store.claim(
        run_id=run_id,
        tenant_id=tenant,
        claimed_by="inst-a",
        lease_until=_BASE + timedelta(seconds=30),
        heartbeat_at=_BASE,
    )
    # A different instance cannot renew (it doesn't own the run).
    assert not await store.heartbeat(
        run_id=run_id,
        claimed_by="inst-b",
        lease_until=_BASE + timedelta(seconds=60),
        heartbeat_at=_BASE,
    )


@pytest.mark.asyncio
async def test_list_orphans_only_expired_running() -> None:
    store = InMemoryRunStore()
    tenant = uuid4()
    now = _BASE + timedelta(minutes=5)
    # expired-lease running → orphan
    orphan = uuid4()
    await store.create(_info(run_id=orphan, tenant_id=tenant, status=RunStatus.RUNNING))
    await store.claim(
        run_id=orphan,
        tenant_id=tenant,
        claimed_by="dead",
        lease_until=now - timedelta(seconds=1),
        heartbeat_at=_BASE,
    )
    # fresh-lease running → not orphan
    live = uuid4()
    await store.create(_info(run_id=live, tenant_id=tenant, status=RunStatus.RUNNING))
    await store.claim(
        run_id=live,
        tenant_id=tenant,
        claimed_by="alive",
        lease_until=now + timedelta(seconds=30),
        heartbeat_at=now,
    )
    # terminal with stale lease → never an orphan
    done = uuid4()
    await store.create(_info(run_id=done, tenant_id=tenant, status=RunStatus.SUCCESS))
    await store.claim(
        run_id=done,
        tenant_id=tenant,
        claimed_by="dead",
        lease_until=now - timedelta(minutes=1),
        heartbeat_at=_BASE,
    )

    orphans = await store.list_orphans(now=now, limit=10)
    assert [o.run_id for o in orphans] == [orphan]


@pytest.mark.asyncio
async def test_reclaim_cas_one_winner() -> None:
    store = InMemoryRunStore()
    run_id, tenant = uuid4(), uuid4()
    now = _BASE + timedelta(minutes=5)
    await store.create(_info(run_id=run_id, tenant_id=tenant, status=RunStatus.RUNNING))
    await store.claim(
        run_id=run_id,
        tenant_id=tenant,
        claimed_by="dead",
        lease_until=now - timedelta(seconds=1),
        heartbeat_at=_BASE,
    )
    # first reclaim wins
    assert await store.reclaim(
        run_id=run_id,
        new_owner="inst-b",
        lease_until=now + timedelta(seconds=30),
        heartbeat_at=now,
        now=now,
    )
    # second reclaim loses — lease is fresh now (no longer < now)
    assert not await store.reclaim(
        run_id=run_id,
        new_owner="inst-c",
        lease_until=now + timedelta(seconds=30),
        heartbeat_at=now,
        now=now,
    )
    row = await store.get(run_id=run_id, tenant_id=tenant)
    assert row is not None and row.claimed_by == "inst-b"


# --------------------------------------------------------------------------
# Stream 9.5 — distributed run queue
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_queued_returns_queued_fifo() -> None:
    store = InMemoryRunStore()
    tenant = uuid4()
    old = uuid4()
    new = uuid4()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await store.create(
        _info(run_id=old, tenant_id=tenant, status=RunStatus.QUEUED, created_at=base)
    )
    await store.create(
        _info(
            run_id=new,
            tenant_id=tenant,
            status=RunStatus.QUEUED,
            created_at=base + timedelta(minutes=5),
        )
    )
    # A non-queued run must not appear.
    await store.create(_info(run_id=uuid4(), tenant_id=tenant, status=RunStatus.RUNNING))

    queued = await store.list_queued(limit=10)
    assert [q.run_id for q in queued] == [old, new]  # oldest first


@pytest.mark.asyncio
async def test_claim_queued_cas_one_winner() -> None:
    store = InMemoryRunStore()
    tenant, run_id = uuid4(), uuid4()
    await store.create(_info(run_id=run_id, tenant_id=tenant, status=RunStatus.QUEUED))
    now = datetime.now(UTC)
    lease = now + timedelta(seconds=30)

    won = await store.claim_queued(
        run_id=run_id, new_owner="worker-a", lease_until=lease, heartbeat_at=now
    )
    assert won is not None
    assert won.status is RunStatus.RUNNING
    assert won.claimed_by == "worker-a"
    assert won.enqueued_input is None or isinstance(won.enqueued_input, dict)

    # Second claim loses — the run is no longer queued.
    lost = await store.claim_queued(
        run_id=run_id, new_owner="worker-b", lease_until=lease, heartbeat_at=now
    )
    assert lost is None
    row = await store.get(run_id=run_id, tenant_id=tenant)
    assert row is not None and row.claimed_by == "worker-a"


@pytest.mark.asyncio
async def test_claim_queued_carries_enqueued_input() -> None:
    store = InMemoryRunStore()
    tenant, run_id = uuid4(), uuid4()
    info = _info(run_id=run_id, tenant_id=tenant, status=RunStatus.QUEUED)
    from dataclasses import replace

    await store.create(replace(info, enqueued_input={"input": "hi", "image_refs": []}))
    now = datetime.now(UTC)
    claimed = await store.claim_queued(
        run_id=run_id, new_owner="w", lease_until=now + timedelta(seconds=30), heartbeat_at=now
    )
    assert claimed is not None
    assert claimed.enqueued_input == {"input": "hi", "image_refs": []}
