"""Integration tests for SqlThreadMetaStore against a real Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlThreadMetaStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.protocol import ThreadStatus

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

SqlStoreFixture = tuple[SqlThreadMetaStore, AsyncEngine]


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield SqlThreadMetaStore(session_factory), engine


@pytest.mark.asyncio
async def test_create_and_get_round_trip(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        thread_id, tenant_id = uuid4(), uuid4()
        meta = await store.create(
            thread_id=thread_id,
            tenant_id=tenant_id,
            created_by="user-1",
            agent_name="demo",
            agent_version="0.1.0",
        )
        assert meta.status == ThreadStatus.ACTIVE

        fetched = await store.get(thread_id, tenant_id=tenant_id)
        assert fetched is not None and fetched.agent_version == "0.1.0"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_filters_by_tenant(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        thread_id, owner, other = uuid4(), uuid4(), uuid4()
        await store.create(thread_id=thread_id, tenant_id=owner, created_by="u")

        assert await store.get(thread_id, tenant_id=other) is None
        assert await store.get(thread_id, tenant_id=owner) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_id_round_trip_and_list_filter(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id, user_a, user_b = uuid4(), uuid4(), uuid4()
        owned = await store.create(
            thread_id=uuid4(), tenant_id=tenant_id, created_by="x", user_id=user_a
        )
        assert owned.user_id == user_a
        await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x", user_id=user_b)
        unowned = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")
        assert unowned.user_id is None

        only_a = await store.list_by_tenant(tenant_id, user_id=user_a)
        assert [m.user_id for m in only_a] == [user_a]
        assert len(await store.list_by_tenant(tenant_id)) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_thread_id_rejected(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        thread_id, tenant_id = uuid4(), uuid4()
        await store.create(thread_id=thread_id, tenant_id=tenant_id, created_by="u")
        with pytest.raises(ValueError, match="already exists"):
            await store.create(thread_id=thread_id, tenant_id=tenant_id, created_by="u")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_by_tenant_pagination_and_status(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id = uuid4()
        threads = [uuid4() for _ in range(5)]
        for t in threads:
            await store.create(thread_id=t, tenant_id=tenant_id, created_by="u")
        await store.update_status(threads[0], ThreadStatus.COMPLETED, tenant_id=tenant_id)

        active = await store.list_by_tenant(tenant_id, status=ThreadStatus.ACTIVE)
        assert len(active) == 4

        page = await store.list_by_tenant(tenant_id, limit=2, offset=0)
        assert len(page) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_status_tenant_isolation(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        thread_id, owner, other = uuid4(), uuid4(), uuid4()
        await store.create(thread_id=thread_id, tenant_id=owner, created_by="u")

        # Bind the awaited result first; `assert await foo()` would be stripped
        # under `python -O` and the side effect (the UPDATE) would silently
        # disappear (CodeQL py/side-effect-in-assert).
        owner_update = await store.update_status(thread_id, ThreadStatus.PAUSED, tenant_id=owner)
        assert owner_update is True
        other_update = await store.update_status(thread_id, ThreadStatus.PAUSED, tenant_id=other)
        assert other_update is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_tenant_isolation(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        thread_id, owner, other = uuid4(), uuid4(), uuid4()
        await store.create(thread_id=thread_id, tenant_id=owner, created_by="u")

        other_delete = await store.delete(thread_id, tenant_id=other)
        assert other_delete is False
        still_accessible = await store.check_access(thread_id, owner)
        assert still_accessible is True

        owner_delete = await store.delete(thread_id, tenant_id=owner)
        assert owner_delete is True
        gone = await store.check_access(thread_id, owner)
        assert gone is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_by_tenant_agent_filters(sql_store: SqlStoreFixture) -> None:
    """Stream H.6 (Mini-ADR H-10) — agent_name / agent_version WHERE clauses."""
    store, engine = sql_store
    try:
        tenant_id = uuid4()
        await store.create(
            thread_id=uuid4(),
            tenant_id=tenant_id,
            created_by="x",
            agent_name="reporter",
            agent_version="1.0.0",
        )
        await store.create(
            thread_id=uuid4(),
            tenant_id=tenant_id,
            created_by="x",
            agent_name="reporter",
            agent_version="2.0.0",
        )
        await store.create(
            thread_id=uuid4(),
            tenant_id=tenant_id,
            created_by="x",
            agent_name="scribe",
            agent_version="1.0.0",
        )

        by_name = await store.list_by_tenant(tenant_id, agent_name="reporter")
        assert {m.agent_version for m in by_name} == {"1.0.0", "2.0.0"}

        by_name_version = await store.list_by_tenant(
            tenant_id, agent_name="reporter", agent_version="2.0.0"
        )
        assert [m.agent_version for m in by_name_version] == ["2.0.0"]

        assert await store.list_by_tenant(tenant_id, agent_name="ghost") == []
        # No filter → all three (regression).
        assert len(await store.list_by_tenant(tenant_id)) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_nonempty_filters_threads_without_runs(sql_store: SqlStoreFixture) -> None:
    """``nonempty=True`` drops threads that never had a run — the empty
    throwaway sessions eager thread-creation left behind."""
    from datetime import UTC, datetime

    from helix_agent.persistence.models.agent_run import AgentRunRow

    store, engine = sql_store
    try:
        tenant_id = uuid4()
        with_run = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")
        await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")  # never ran

        # Seed one run so ``with_run`` counts as a real (non-empty) thread.
        session_factory = create_async_session_factory(engine)
        now = datetime.now(UTC)
        async with session_factory() as session:
            session.add(
                AgentRunRow(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    thread_id=with_run.thread_id,
                    status="success",
                    on_disconnect="cancel",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        # No filter → both threads (regression).
        assert len(await store.list_by_tenant(tenant_id)) == 2
        # nonempty → only the thread that has a run.
        filtered = await store.list_by_tenant(tenant_id, nonempty=True)
        assert [t.thread_id for t in filtered] == [with_run.thread_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_title_and_tenant_isolation(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        thread_id, owner, other = uuid4(), uuid4(), uuid4()
        created = await store.create(thread_id=thread_id, tenant_id=owner, created_by="u")
        assert created.title is None

        other_update = await store.update_title(thread_id, "hijack", tenant_id=other)
        assert other_update is False
        owner_update = await store.update_title(thread_id, "季度报告", tenant_id=owner)
        assert owner_update is True

        fetched = await store.get(thread_id, tenant_id=owner)
        assert fetched is not None and fetched.title == "季度报告"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_q_search_escapes_like_wildcards(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id = uuid4()
        report = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")
        literal = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")
        await store.update_title(report.thread_id, "Quarterly Report", tenant_id=tenant_id)
        await store.update_title(literal.thread_id, "50%_off deal", tenant_id=tenant_id)
        await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")  # untitled

        by_word = await store.list_by_tenant(tenant_id, q="report")
        assert [m.thread_id for m in by_word] == [report.thread_id]

        # ``%`` / ``_`` in the term must match literally, not as LIKE wildcards.
        literal_hit = await store.list_by_tenant(tenant_id, q="50%_off")
        assert [m.thread_id for m in literal_hit] == [literal.thread_id]
        assert await store.list_by_tenant(tenant_id, q="zzz") == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_archived_excluded_by_default(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant_id = uuid4()
        keep = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")
        archived = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="u")
        did_archive = await store.update_status(
            archived.thread_id, ThreadStatus.ARCHIVED, tenant_id=tenant_id
        )
        assert did_archive is True

        default = await store.list_by_tenant(tenant_id)
        assert [m.thread_id for m in default] == [keep.thread_id]

        with_archived = await store.list_by_tenant(tenant_id, include_archived=True)
        assert {m.thread_id for m in with_archived} == {keep.thread_id, archived.thread_id}

        only_archived = await store.list_by_tenant(tenant_id, status=ThreadStatus.ARCHIVED)
        assert [m.thread_id for m in only_archived] == [archived.thread_id]
    finally:
        await engine.dispose()
