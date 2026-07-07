"""Integration tests for :class:`SqlAgentSpecStore` against a real Postgres.

Mirrors the ``InMemoryAgentSpecStore`` unit suite, run against the
Alembic schema. Each test uses a fresh ``tenant_id`` because the
testcontainers Postgres is shared across the session.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.agent_spec import DuplicateAgentSpecError, SqlAgentSpecStore
from expert_work.protocol import AgentSpec, AgentSpecStatus

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

_BASE_SPEC: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "code-reviewer", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are a reviewer"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _spec(*, version: str = "1.0.0", name: str = "code-reviewer") -> AgentSpec:
    doc = deepcopy(_BASE_SPEC)
    doc["metadata"]["version"] = version
    doc["metadata"]["name"] = name
    return AgentSpec.model_validate(doc)


def _sha() -> str:
    return "a" * 64


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


SqlStoreFixture = tuple[SqlAgentSpecStore, AsyncEngine]


@pytest.fixture
def sql_store(postgres_container: PostgresContainer) -> Iterator[SqlStoreFixture]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    yield SqlAgentSpecStore(create_async_session_factory(engine)), engine


@pytest.mark.asyncio
async def test_create_then_get_round_trip(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        record = await store.create(
            tenant_id=tenant, spec=_spec(), spec_sha256=_sha(), created_by="alice"
        )
        assert record.status is AgentSpecStatus.ACTIVE
        assert isinstance(record.id, UUID)

        fetched = await store.get(tenant_id=tenant, name="code-reviewer", version="1.0.0")
        assert fetched is not None
        assert fetched.id == record.id
        assert fetched.spec_sha256 == _sha()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_create_raises(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        await store.create(tenant_id=tenant, spec=_spec(), spec_sha256=_sha(), created_by="a")
        with pytest.raises(DuplicateAgentSpecError):
            await store.create(tenant_id=tenant, spec=_spec(), spec_sha256=_sha(), created_by="a")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_filters_by_tenant(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        owner, other = uuid4(), uuid4()
        await store.create(tenant_id=owner, spec=_spec(), spec_sha256=_sha(), created_by="a")
        assert await store.get(tenant_id=other, name="code-reviewer", version="1.0.0") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_filters_by_name_newest_first(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        await store.create(
            tenant_id=tenant, spec=_spec(version="1.0.0"), spec_sha256=_sha(), created_by="a"
        )
        await store.create(
            tenant_id=tenant, spec=_spec(version="1.0.1"), spec_sha256=_sha(), created_by="a"
        )
        await store.create(
            tenant_id=tenant, spec=_spec(name="other"), spec_sha256=_sha(), created_by="a"
        )
        rows = await store.list_by_tenant(tenant_id=tenant, name="code-reviewer")
        assert [r.version for r in rows] == ["1.0.1", "1.0.0"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_spec_replaces_payload(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        await store.create(tenant_id=tenant, spec=_spec(), spec_sha256=_sha(), created_by="a")
        new_doc = deepcopy(_BASE_SPEC)
        new_doc["spec"]["system_prompt"]["template"] = "updated prompt"
        updated = await store.update_spec(
            tenant_id=tenant,
            name="code-reviewer",
            version="1.0.0",
            spec=AgentSpec.model_validate(new_doc),
            spec_sha256="b" * 64,
            updated_by="alice",
        )
        assert updated is not None
        assert updated.record.spec.spec.system_prompt.template == "updated prompt"
        assert updated.record.spec_sha256 == "b" * 64
        # Stream HX-5 -- create wrote revision 1, this update revision 2.
        assert updated.revision == 2
        assert updated.prev_sha256 == _sha()
        history = await store.list_revisions(
            tenant_id=tenant, name="code-reviewer", version="1.0.0"
        )
        assert [r.revision for r in history] == [2, 1]
        assert history[0].actor_id == "alice"
        assert history[1].actor_id == "a"

        # Same-sha update: recorded no-op, no new revision.
        noop = await store.update_spec(
            tenant_id=tenant,
            name="code-reviewer",
            version="1.0.0",
            spec=AgentSpec.model_validate(new_doc),
            spec_sha256="b" * 64,
            updated_by="alice",
        )
        assert noop is not None and noop.revision is None
        assert (
            len(await store.list_revisions(tenant_id=tenant, name="code-reviewer", version="1.0.0"))
            == 2
        )

        one = await store.get_revision(
            tenant_id=tenant, name="code-reviewer", version="1.0.0", revision=1
        )
        assert one is not None and one.spec_sha256 == _sha()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_spec_returns_none_when_missing(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        result = await store.update_spec(
            tenant_id=uuid4(),
            name="missing",
            version="9.9.9",
            spec=_spec(),
            spec_sha256=_sha(),
            updated_by="a",
        )
        assert result is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_soft_delete_hides_from_get(sql_store: SqlStoreFixture) -> None:
    store, engine = sql_store
    try:
        tenant = uuid4()
        await store.create(tenant_id=tenant, spec=_spec(), spec_sha256=_sha(), created_by="a")
        deleted = await store.update_status(
            tenant_id=tenant,
            name="code-reviewer",
            version="1.0.0",
            status=AgentSpecStatus.DELETED,
        )
        assert deleted is not None and deleted.status is AgentSpecStatus.DELETED

        assert await store.get(tenant_id=tenant, name="code-reviewer", version="1.0.0") is None
        fetched = await store.get(
            tenant_id=tenant, name="code-reviewer", version="1.0.0", include_deleted=True
        )
        assert fetched is not None and fetched.status is AgentSpecStatus.DELETED
    finally:
        await engine.dispose()
