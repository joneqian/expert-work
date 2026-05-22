"""Integration tests for SqlTriggerStore against a real Postgres — J.10."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlTriggerStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.protocol import TriggerKind, TriggerRecord

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def trigger_store(postgres_container: PostgresContainer) -> Iterator[SqlTriggerStore]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    yield SqlTriggerStore(create_async_session_factory(engine))


def _record(
    *,
    trigger_id: UUID | None = None,
    tenant_id: UUID | None = None,
    agent_name: str = "reporter",
    name: str = "nightly",
    kind: TriggerKind = "cron",
    enabled: bool = True,
) -> TriggerRecord:
    config: dict[str, object] = {"expr": "0 9 * * *"} if kind == "cron" else {}
    return TriggerRecord(
        id=trigger_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        agent_name=agent_name,
        agent_version="1.0.0",
        name=name,
        kind=kind,
        config=config,
        enabled=enabled,
        source="api",
        webhook_secret_hash="sha256:abc" if kind == "webhook" else None,
        created_at=_BASE,
        updated_at=_BASE,
    )


@pytest.mark.asyncio
async def test_create_then_get_round_trips(trigger_store: SqlTriggerStore) -> None:
    tid, tenant = uuid4(), uuid4()
    await trigger_store.create(
        _record(trigger_id=tid, tenant_id=tenant, kind="webhook", name="hook")
    )

    fetched = await trigger_store.get(trigger_id=tid, tenant_id=tenant)
    assert fetched is not None
    assert fetched.id == tid
    assert fetched.kind == "webhook"
    assert fetched.webhook_secret_hash == "sha256:abc"
    assert fetched.source == "api"


@pytest.mark.asyncio
async def test_get_unknown_returns_none(trigger_store: SqlTriggerStore) -> None:
    assert await trigger_store.get(trigger_id=uuid4(), tenant_id=uuid4()) is None


@pytest.mark.asyncio
async def test_get_cross_tenant_returns_none(trigger_store: SqlTriggerStore) -> None:
    tid, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await trigger_store.create(_record(trigger_id=tid, tenant_id=tenant_a))

    assert await trigger_store.get(trigger_id=tid, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_duplicate_name_per_agent_violates_unique(
    trigger_store: SqlTriggerStore,
) -> None:
    tenant = uuid4()
    await trigger_store.create(_record(tenant_id=tenant, agent_name="reporter", name="nightly"))
    with pytest.raises(IntegrityError):
        await trigger_store.create(_record(tenant_id=tenant, agent_name="reporter", name="nightly"))


@pytest.mark.asyncio
async def test_list_by_agent_filters(trigger_store: SqlTriggerStore) -> None:
    tenant = uuid4()
    await trigger_store.create(_record(tenant_id=tenant, agent_name="reporter", name="a"))
    await trigger_store.create(_record(tenant_id=tenant, agent_name="reporter", name="b"))
    await trigger_store.create(_record(tenant_id=tenant, agent_name="auditor", name="a"))

    listed = await trigger_store.list_by_agent(tenant_id=tenant, agent_name="reporter")
    assert {r.name for r in listed} == {"a", "b"}


@pytest.mark.asyncio
async def test_list_enabled_cron_cross_tenant_kind_filtered(
    trigger_store: SqlTriggerStore,
) -> None:
    await trigger_store.create(_record(tenant_id=uuid4(), name="c1", kind="cron", enabled=True))
    await trigger_store.create(_record(tenant_id=uuid4(), name="c3", kind="cron", enabled=False))
    await trigger_store.create(_record(tenant_id=uuid4(), name="w1", kind="webhook"))

    listed = await trigger_store.list_enabled_cron()
    names = {r.name for r in listed}
    assert "c1" in names
    assert "c3" not in names  # disabled
    assert "w1" not in names  # webhook


@pytest.mark.asyncio
async def test_update_replaces_mutable_fields(trigger_store: SqlTriggerStore) -> None:
    tid, tenant = uuid4(), uuid4()
    await trigger_store.create(_record(trigger_id=tid, tenant_id=tenant, enabled=True))

    rec = await trigger_store.get(trigger_id=tid, tenant_id=tenant)
    assert rec is not None
    fired_at = _BASE.replace(hour=9)
    updated = rec.model_copy(update={"enabled": False, "last_fired_at": fired_at})
    did_update = await trigger_store.update(updated)
    assert did_update is True

    again = await trigger_store.get(trigger_id=tid, tenant_id=tenant)
    assert again is not None
    assert again.enabled is False
    assert again.last_fired_at == fired_at


@pytest.mark.asyncio
async def test_update_unknown_returns_false(trigger_store: SqlTriggerStore) -> None:
    did_update = await trigger_store.update(_record())
    assert did_update is False


@pytest.mark.asyncio
async def test_delete(trigger_store: SqlTriggerStore) -> None:
    tid, tenant = uuid4(), uuid4()
    await trigger_store.create(_record(trigger_id=tid, tenant_id=tenant))

    deleted = await trigger_store.delete(trigger_id=tid, tenant_id=tenant)
    assert deleted is True
    assert await trigger_store.get(trigger_id=tid, tenant_id=tenant) is None
    deleted_again = await trigger_store.delete(trigger_id=tid, tenant_id=tenant)
    assert deleted_again is False
