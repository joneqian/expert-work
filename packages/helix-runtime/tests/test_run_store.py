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
