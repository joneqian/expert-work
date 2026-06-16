"""Integration tests for ``SqlRunStore`` against a real Postgres — Mini-ADR J-41."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.runtime.runs import DisconnectMode, RunInfo, RunStatus, SqlRunStore

pytestmark = pytest.mark.integration

PERSISTENCE_ROOT = Path(__file__).resolve().parents[2] / "helix-persistence"
ALEMBIC_INI = PERSISTENCE_ROOT / "alembic.ini"

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def run_store(postgres_container: PostgresContainer) -> Iterator[SqlRunStore]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine: AsyncEngine = create_async_engine_from_config(
        DatabaseConfig(dsn=_async_dsn(postgres_container))
    )
    yield SqlRunStore(create_async_session_factory(engine))


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
async def test_create_then_get_round_trips(run_store: SqlRunStore) -> None:
    run_id, tenant_id, user_id = uuid4(), uuid4(), uuid4()
    await run_store.create(
        _info(run_id=run_id, tenant_id=tenant_id, user_id=user_id, status=RunStatus.RUNNING)
    )

    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.run_id == run_id
    assert fetched.user_id == user_id
    assert fetched.status is RunStatus.RUNNING
    assert fetched.on_disconnect is DisconnectMode.CANCEL
    assert fetched.is_resume is False
    assert fetched.finished_at is None


@pytest.mark.asyncio
async def test_get_unknown_returns_none(run_store: SqlRunStore) -> None:
    assert await run_store.get(run_id=uuid4(), tenant_id=uuid4()) is None


@pytest.mark.asyncio
async def test_get_cross_tenant_returns_none(run_store: SqlRunStore) -> None:
    run_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_a))

    assert await run_store.get(run_id=run_id, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_set_status_updates_row(run_store: SqlRunStore) -> None:
    run_id, tenant_id = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_id))

    finished = _BASE + timedelta(seconds=12)
    hit = await run_store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.SUCCESS,
        updated_at=finished,
        finished_at=finished,
    )
    assert hit is True
    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.status is RunStatus.SUCCESS
    assert fetched.finished_at == finished


@pytest.mark.asyncio
async def test_set_status_error_persists_detail(run_store: SqlRunStore) -> None:
    run_id, tenant_id = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_id))

    await run_store.set_status(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.ERROR,
        updated_at=_BASE,
        error="provider 503",
        finished_at=_BASE,
    )
    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.status is RunStatus.ERROR
    assert fetched.error == "provider 503"


@pytest.mark.asyncio
async def test_set_status_unknown_returns_false(run_store: SqlRunStore) -> None:
    miss = await run_store.set_status(
        run_id=uuid4(),
        tenant_id=uuid4(),
        status=RunStatus.SUCCESS,
        updated_at=_BASE,
    )
    assert miss is False


@pytest.mark.asyncio
async def test_list_by_thread_filters_and_sorts(run_store: SqlRunStore) -> None:
    thread_id, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    newer, older = uuid4(), uuid4()
    await run_store.create(
        _info(
            run_id=newer,
            tenant_id=tenant_a,
            thread_id=thread_id,
            created_at=_BASE + timedelta(minutes=1),
        )
    )
    await run_store.create(
        _info(run_id=older, tenant_id=tenant_a, thread_id=thread_id, created_at=_BASE)
    )
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_b, thread_id=thread_id))

    listed = await run_store.list_by_thread(thread_id=thread_id, tenant_id=tenant_a)
    assert [r.run_id for r in listed] == [older, newer]


# ---------------------------------------------------------------------------
# Stream H.3 PR 1 — list_for_tenant / list_all_tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_tenant_orders_desc_and_filters_by_tenant(
    run_store: SqlRunStore,
) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    oldest, newest = uuid4(), uuid4()
    await run_store.create(_info(run_id=oldest, tenant_id=tenant_a, created_at=_BASE))
    await run_store.create(
        _info(run_id=newest, tenant_id=tenant_a, created_at=_BASE + timedelta(minutes=1))
    )
    # tenant B row must not leak.
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_b))

    listed = await run_store.list_for_tenant(tenant_id=tenant_a)
    assert [r.run_id for r in listed] == [newest, oldest]
    assert all(r.tenant_id == tenant_a for r in listed)


@pytest.mark.asyncio
async def test_list_for_tenant_status_filter(run_store: SqlRunStore) -> None:
    tenant_id = uuid4()
    paused_id = uuid4()
    await run_store.create(_info(run_id=paused_id, tenant_id=tenant_id, status=RunStatus.PAUSED))
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_id, status=RunStatus.SUCCESS))

    paused = await run_store.list_for_tenant(tenant_id=tenant_id, status=RunStatus.PAUSED)
    assert [r.run_id for r in paused] == [paused_id]


@pytest.mark.asyncio
async def test_list_for_tenant_offset_limit(run_store: SqlRunStore) -> None:
    tenant_id = uuid4()
    ids = []
    for i in range(5):
        rid = uuid4()
        ids.append(rid)
        await run_store.create(
            _info(run_id=rid, tenant_id=tenant_id, created_at=_BASE + timedelta(minutes=i))
        )
    expected_desc = list(reversed(ids))

    page1 = await run_store.list_for_tenant(tenant_id=tenant_id, limit=2, offset=0)
    page2 = await run_store.list_for_tenant(tenant_id=tenant_id, limit=2, offset=2)
    page3 = await run_store.list_for_tenant(tenant_id=tenant_id, limit=2, offset=4)
    assert [r.run_id for r in page1] == expected_desc[:2]
    assert [r.run_id for r in page2] == expected_desc[2:4]
    assert [r.run_id for r in page3] == expected_desc[4:5]


@pytest.mark.asyncio
async def test_list_all_tenants_returns_runs_across_tenants(
    run_store: SqlRunStore,
) -> None:
    """The test container's postgres user has BYPASSRLS, so prior tests
    in the same session may have left rows. Assert ``tenant_a`` and
    ``tenant_b`` are *both visible* as a subset rather than exact match."""
    tenant_a, tenant_b = uuid4(), uuid4()
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_a))
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_b))

    listed = await run_store.list_all_tenants(limit=500)
    tenants = {r.tenant_id for r in listed}
    assert tenant_a in tenants
    assert tenant_b in tenants


# ---------------------------------------------------------------------------
# Stream H.3 PR 2 — set_trace_id (Mini-ADR H-9.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_trace_id_writes_and_reads_back_sql(run_store: SqlRunStore) -> None:
    run_id, tenant_id = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant_id))

    ok = await run_store.set_trace_id(run_id=run_id, tenant_id=tenant_id, trace_id="cafef00d" * 4)
    assert ok is True

    fetched = await run_store.get(run_id=run_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.trace_id == "cafef00d" * 4


@pytest.mark.asyncio
async def test_set_trace_id_unknown_run_returns_false_sql(
    run_store: SqlRunStore,
) -> None:
    ok = await run_store.set_trace_id(run_id=uuid4(), tenant_id=uuid4(), trace_id="abc")
    assert ok is False


@pytest.mark.asyncio
async def test_list_for_tenant_thread_ids_filter_sql(run_store: SqlRunStore) -> None:
    """Stream H.6 (Mini-ADR H-10) — ``WHERE thread_id IN (...)`` + empty fast-path."""
    tenant_id = uuid4()
    thread_a, thread_b = uuid4(), uuid4()
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_id, thread_id=thread_a))
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_id, thread_id=thread_a))
    await run_store.create(_info(run_id=uuid4(), tenant_id=tenant_id, thread_id=thread_b))

    subset = await run_store.list_for_tenant(tenant_id=tenant_id, thread_ids=[thread_a])
    assert len(subset) == 2
    assert {r.thread_id for r in subset} == {thread_a}

    # Empty collection → no rows (the agent has no threads), not "no filter".
    assert await run_store.list_for_tenant(tenant_id=tenant_id, thread_ids=[]) == []
    # None regression — unfiltered list unchanged.
    assert len(await run_store.list_for_tenant(tenant_id=tenant_id)) == 3


# --- Stream 9.4 (HA failover) — ownership lease on real Postgres -------------


@pytest.mark.asyncio
async def test_claim_heartbeat_and_orphan_lease_round_trip(run_store: SqlRunStore) -> None:
    run_id, tenant = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant, status=RunStatus.RUNNING))
    t0 = datetime.now(UTC)
    assert await run_store.claim(
        run_id=run_id,
        tenant_id=tenant,
        claimed_by="inst-a",
        lease_until=t0 + timedelta(seconds=30),
        heartbeat_at=t0,
    )
    # owner renews; a non-owner cannot
    t1 = t0 + timedelta(seconds=10)
    assert await run_store.heartbeat(
        run_id=run_id,
        claimed_by="inst-a",
        lease_until=t1 + timedelta(seconds=30),
        heartbeat_at=t1,
    )
    assert not await run_store.heartbeat(
        run_id=run_id,
        claimed_by="inst-b",
        lease_until=t1 + timedelta(seconds=60),
        heartbeat_at=t1,
    )
    row = await run_store.get(run_id=run_id, tenant_id=tenant)
    assert row is not None and row.claimed_by == "inst-a" and row.reclaim_count == 0


@pytest.mark.asyncio
async def test_concurrent_reclaim_exactly_one_winner(run_store: SqlRunStore) -> None:
    """True DB concurrency — the reclaim CAS row-lock serialises sweepers."""
    import asyncio

    run_id, tenant = uuid4(), uuid4()
    await run_store.create(_info(run_id=run_id, tenant_id=tenant, status=RunStatus.RUNNING))
    now = datetime.now(UTC)
    # expired lease → orphan
    await run_store.claim(
        run_id=run_id,
        tenant_id=tenant,
        claimed_by="dead",
        lease_until=now - timedelta(seconds=1),
        heartbeat_at=now,
    )

    async def _reclaim(n: int) -> bool:
        return await run_store.reclaim(
            run_id=run_id,
            new_owner=f"sweeper-{n}",
            lease_until=now + timedelta(seconds=30),
            heartbeat_at=now,
            now=now,
        )

    results = await asyncio.gather(*(_reclaim(i) for i in range(8)))
    assert sum(1 for r in results if r) == 1  # exactly one sweeper wins
    row = await run_store.get(run_id=run_id, tenant_id=tenant)
    assert row is not None and row.reclaim_count == 1  # incremented exactly once


@pytest.mark.asyncio
async def test_list_orphans_finds_only_expired_running(run_store: SqlRunStore) -> None:
    tenant = uuid4()
    now = datetime.now(UTC)
    orphan = uuid4()
    await run_store.create(_info(run_id=orphan, tenant_id=tenant, status=RunStatus.RUNNING))
    await run_store.claim(
        run_id=orphan,
        tenant_id=tenant,
        claimed_by="dead",
        lease_until=now - timedelta(seconds=1),
        heartbeat_at=now,
    )
    live = uuid4()
    await run_store.create(_info(run_id=live, tenant_id=tenant, status=RunStatus.RUNNING))
    await run_store.claim(
        run_id=live,
        tenant_id=tenant,
        claimed_by="alive",
        lease_until=now + timedelta(seconds=30),
        heartbeat_at=now,
    )
    found = await run_store.list_orphans(now=now, limit=10)
    assert orphan in [o.run_id for o in found]
    assert live not in [o.run_id for o in found]


@pytest.mark.asyncio
async def test_concurrent_claim_queued_exactly_one_winner(run_store: SqlRunStore) -> None:
    """True DB concurrency — the claim CAS row-lock serialises queue workers."""
    import asyncio
    from dataclasses import replace

    run_id, tenant = uuid4(), uuid4()
    info = _info(run_id=run_id, tenant_id=tenant, status=RunStatus.QUEUED)
    await run_store.create(replace(info, enqueued_input={"input": "go", "image_refs": []}))
    now = datetime.now(UTC)

    async def _claim(n: int):
        return await run_store.claim_queued(
            run_id=run_id,
            new_owner=f"worker-{n}",
            lease_until=now + timedelta(seconds=30),
            heartbeat_at=now,
        )

    results = await asyncio.gather(*(_claim(i) for i in range(16)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1  # exactly one worker claims the queued run
    assert winners[0].enqueued_input == {"input": "go", "image_refs": []}
    row = await run_store.get(run_id=run_id, tenant_id=tenant)
    assert row is not None and row.status is RunStatus.RUNNING


@pytest.mark.asyncio
async def test_list_queued_round_trips_enqueued_input(run_store: SqlRunStore) -> None:
    from dataclasses import replace

    tenant, run_id = uuid4(), uuid4()
    info = _info(run_id=run_id, tenant_id=tenant, status=RunStatus.QUEUED)
    await run_store.create(
        replace(info, enqueued_input={"input": "hi", "untrusted_content": ["x"]})
    )

    queued = await run_store.list_queued(limit=10)
    assert any(q.run_id == run_id for q in queued)
    match = next(q for q in queued if q.run_id == run_id)
    assert match.enqueued_input == {"input": "hi", "untrusted_content": ["x"]}
