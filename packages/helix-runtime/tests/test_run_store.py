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
