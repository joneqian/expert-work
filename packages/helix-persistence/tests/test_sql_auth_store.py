"""Integration tests for the Stream C.3 SQL auth stores.

Covers :class:`SqlServiceAccountStore`, :class:`SqlApiKeyStore`, and
:class:`SqlRoleBindingStore` against a real Postgres. Each test uses
fresh ``tenant_id`` / ``prefix`` values because the testcontainers
Postgres is shared across the session.
"""

from __future__ import annotations

from collections.abc import Iterator
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
from helix_agent.persistence.auth import (
    SqlApiKeyStore,
    SqlRoleBindingStore,
    SqlServiceAccountStore,
)
from helix_agent.persistence.auth.base import (
    DuplicateApiKeyPrefixError,
    DuplicateRoleBindingError,
    DuplicateServiceAccountError,
)
from helix_agent.protocol import ApiKeyScope, Role

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


AuthStores = tuple[SqlServiceAccountStore, SqlApiKeyStore, SqlRoleBindingStore, AsyncEngine]


@pytest.fixture
def auth_stores(postgres_container: PostgresContainer) -> Iterator[AuthStores]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")

    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    session_factory = create_async_session_factory(engine)
    yield (
        SqlServiceAccountStore(session_factory),
        SqlApiKeyStore(session_factory),
        SqlRoleBindingStore(session_factory),
        engine,
    )


# ---------------------------------------------------------------------------
# ServiceAccount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_account_create_get_round_trip(auth_stores: AuthStores) -> None:
    sa_store, _, _, engine = auth_stores
    try:
        tenant = uuid4()
        created = await sa_store.create(
            tenant_id=tenant, name="ci-bot", description="CI runner", created_by="admin"
        )
        assert isinstance(created.id, UUID)
        assert created.is_active is True

        fetched = await sa_store.get(tenant_id=tenant, service_account_id=created.id)
        assert fetched is not None and fetched.name == "ci-bot"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_service_account_duplicate_name_raises(auth_stores: AuthStores) -> None:
    sa_store, _, _, engine = auth_stores
    try:
        tenant = uuid4()
        await sa_store.create(tenant_id=tenant, name="dup", description="", created_by="a")
        with pytest.raises(DuplicateServiceAccountError):
            await sa_store.create(tenant_id=tenant, name="dup", description="", created_by="a")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_service_account_get_filters_by_tenant(auth_stores: AuthStores) -> None:
    sa_store, _, _, engine = auth_stores
    try:
        owner, other = uuid4(), uuid4()
        created = await sa_store.create(
            tenant_id=owner, name="scoped", description="", created_by="a"
        )
        assert await sa_store.get(tenant_id=other, service_account_id=created.id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_service_account_delete(auth_stores: AuthStores) -> None:
    sa_store, _, _, engine = auth_stores
    try:
        tenant = uuid4()
        created = await sa_store.create(
            tenant_id=tenant, name="tmp", description="", created_by="a"
        )
        assert await sa_store.delete(tenant_id=tenant, service_account_id=created.id) is True
        assert await sa_store.delete(tenant_id=tenant, service_account_id=created.id) is False
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# ApiKey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_create_and_prefix_lookup(auth_stores: AuthStores) -> None:
    sa_store, key_store, _, engine = auth_stores
    try:
        tenant = uuid4()
        sa = await sa_store.create(tenant_id=tenant, name="k-bot", description="", created_by="a")
        prefix = uuid4().hex[:16]
        created = await key_store.create(
            tenant_id=tenant,
            service_account_id=sa.id,
            prefix=prefix,
            secret_hash="argon2-hash",
            scopes=[ApiKeyScope.READ, ApiKeyScope.WRITE],
            expires_at=None,
            created_by="a",
        )
        assert created.revoked_at is None

        looked_up = await key_store.get_by_prefix(prefix=prefix)
        assert looked_up is not None
        assert looked_up.id == created.id
        assert set(looked_up.scopes) == {ApiKeyScope.READ, ApiKeyScope.WRITE}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_api_key_duplicate_prefix_raises(auth_stores: AuthStores) -> None:
    sa_store, key_store, _, engine = auth_stores
    try:
        tenant = uuid4()
        sa = await sa_store.create(tenant_id=tenant, name="dup-key", description="", created_by="a")
        prefix = uuid4().hex[:16]
        await key_store.create(
            tenant_id=tenant,
            service_account_id=sa.id,
            prefix=prefix,
            secret_hash="h",
            scopes=[ApiKeyScope.READ],
            expires_at=None,
            created_by="a",
        )
        with pytest.raises(DuplicateApiKeyPrefixError):
            await key_store.create(
                tenant_id=tenant,
                service_account_id=sa.id,
                prefix=prefix,
                secret_hash="h",
                scopes=[ApiKeyScope.READ],
                expires_at=None,
                created_by="a",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_api_key_revoke_and_list(auth_stores: AuthStores) -> None:
    sa_store, key_store, _, engine = auth_stores
    try:
        tenant = uuid4()
        sa = await sa_store.create(tenant_id=tenant, name="rev", description="", created_by="a")
        created = await key_store.create(
            tenant_id=tenant,
            service_account_id=sa.id,
            prefix=uuid4().hex[:16],
            secret_hash="h",
            scopes=[ApiKeyScope.ADMIN],
            expires_at=None,
            created_by="a",
        )
        keys = await key_store.list_by_service_account(tenant_id=tenant, service_account_id=sa.id)
        assert [k.id for k in keys] == [created.id]

        assert await key_store.revoke(tenant_id=tenant, api_key_id=created.id) is True
        revoked = await key_store.get_by_prefix(prefix=created.prefix)
        assert revoked is not None and revoked.revoked_at is not None
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# RoleBinding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_binding_create_and_list(auth_stores: AuthStores) -> None:
    _, _, rb_store, engine = auth_stores
    try:
        tenant, subject = uuid4(), uuid4()
        created = await rb_store.create(
            subject_type="service_account",
            subject_id=subject,
            tenant_id=tenant,
            role=Role.ADMIN,
            granted_by="root",
        )
        assert created.role is Role.ADMIN

        for_subject = await rb_store.list_for_subject(
            subject_type="service_account", subject_id=subject, tenant_id=tenant
        )
        assert [b.id for b in for_subject] == [created.id]

        for_tenant = await rb_store.list_for_tenant(tenant_id=tenant)
        assert [b.id for b in for_tenant] == [created.id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_role_binding_duplicate_raises(auth_stores: AuthStores) -> None:
    _, _, rb_store, engine = auth_stores
    try:
        tenant, subject = uuid4(), uuid4()
        await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=tenant,
            role=Role.VIEWER,
            granted_by="root",
        )
        with pytest.raises(DuplicateRoleBindingError):
            await rb_store.create(
                subject_type="user",
                subject_id=subject,
                tenant_id=tenant,
                role=Role.VIEWER,
                granted_by="root",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_role_binding_delete(auth_stores: AuthStores) -> None:
    _, _, rb_store, engine = auth_stores
    try:
        tenant, subject = uuid4(), uuid4()
        created = await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=tenant,
            role=Role.OPERATOR,
            granted_by="root",
        )
        assert await rb_store.delete(tenant_id=tenant, role_binding_id=created.id) is True
        assert await rb_store.delete(tenant_id=tenant, role_binding_id=created.id) is False
    finally:
        await engine.dispose()
