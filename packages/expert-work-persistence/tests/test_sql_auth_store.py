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

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.auth import (
    SqlApiKeyStore,
    SqlRoleBindingStore,
    SqlServiceAccountStore,
)
from expert_work.persistence.auth.base import (
    DuplicateApiKeyPrefixError,
    DuplicateRoleBindingError,
    DuplicateServiceAccountError,
)
from expert_work.protocol import ApiKeyScope, BindingConditions, Role

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
        # Bind the mutating call before asserting — ``assert`` is stripped
        # under ``python -O`` (CodeQL py/side-effect-in-assert).
        first = await sa_store.delete(tenant_id=tenant, service_account_id=created.id)
        assert first is True
        second = await sa_store.delete(tenant_id=tenant, service_account_id=created.id)
        assert second is False
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

        # Bind the mutating call before asserting (CodeQL py/side-effect-in-assert).
        was_revoked = await key_store.revoke(tenant_id=tenant, api_key_id=created.id)
        assert was_revoked is True
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
@pytest.mark.integration
async def test_role_binding_conditions_round_trip(auth_stores: AuthStores) -> None:
    """Stream 8.5 — a tenant binding's ABAC conditions persist + reload as JSONB.

    Also guards the ``none_as_null`` invariant: an unconditioned binding stores
    SQL NULL (not JSONB ``null``), so the platform-scope CHECK is satisfiable and
    the reload yields ``conditions is None``.
    """
    _, _, rb_store, engine = auth_stores
    try:
        tenant, subject = uuid4(), uuid4()
        conditions = BindingConditions(
            resource_ids=("agent-foo",), labels={"team": "支持"}, owner_only=True
        )
        created = await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=tenant,
            role=Role.OPERATOR,
            granted_by="root",
            conditions=conditions,
        )
        assert created.conditions == conditions
        reloaded = await rb_store.list_for_subject(
            subject_type="user", subject_id=subject, tenant_id=tenant
        )
        assert reloaded[0].conditions == conditions

        # An unconditioned binding stores SQL NULL → reloads as None.
        plain = await rb_store.create(
            subject_type="user",
            subject_id=uuid4(),
            tenant_id=tenant,
            role=Role.VIEWER,
            granted_by="root",
        )
        assert plain.conditions is None
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
        # Bind the mutating call before asserting (CodeQL py/side-effect-in-assert).
        first = await rb_store.delete(tenant_id=tenant, role_binding_id=created.id)
        assert first is True
        second = await rb_store.delete(tenant_id=tenant, role_binding_id=created.id)
        assert second is False
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# RoleBinding — Stream N platform_scope (CHECK constraint + partial UNIQUE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_scope_binding_round_trip(auth_stores: AuthStores) -> None:
    """Create + retrieve + list a SYSTEM_ADMIN platform-scope binding."""
    _, _, rb_store, engine = auth_stores
    try:
        subject = uuid4()
        binding = await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            granted_by="root",
        )
        assert binding.platform_scope is True
        assert binding.tenant_id is None

        admin = await rb_store.get_platform_admin_for_subject(
            subject_type="user", subject_id=subject
        )
        assert admin is not None
        assert admin.id == binding.id

        platform = await rb_store.list_platform_scope()
        assert binding.id in [b.id for b in platform]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_platform_scope_partial_unique_blocks_duplicate(auth_stores: AuthStores) -> None:
    """Partial UNIQUE index — each subject has at most one platform binding."""
    _, _, rb_store, engine = auth_stores
    try:
        subject = uuid4()
        await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            granted_by="root",
        )
        with pytest.raises(DuplicateRoleBindingError):
            await rb_store.create(
                subject_type="user",
                subject_id=subject,
                tenant_id=None,
                role=Role.SYSTEM_ADMIN,
                platform_scope=True,
                granted_by="root",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_platform_scope_subject_can_also_have_tenant_binding(
    auth_stores: AuthStores,
) -> None:
    """The partial UNIQUE only restricts duplicate platform bindings; the same
    subject can still hold a separate tenant-scope binding."""
    _, _, rb_store, engine = auth_stores
    try:
        subject, tenant = uuid4(), uuid4()
        tenant_b = await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=tenant,
            role=Role.ADMIN,
            granted_by="root",
        )
        platform_b = await rb_store.create(
            subject_type="user",
            subject_id=subject,
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            granted_by="root",
        )
        assert tenant_b.id != platform_b.id

        all_for_subject = await rb_store.list_for_subject(subject_type="user", subject_id=subject)
        assert {b.id for b in all_for_subject} == {tenant_b.id, platform_b.id}

        # list_for_tenant must NOT return the platform binding.
        for_tenant = await rb_store.list_for_tenant(tenant_id=tenant)
        assert [b.id for b in for_tenant] == [tenant_b.id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_db_check_rejects_platform_scope_with_wrong_role(auth_stores: AuthStores) -> None:
    """DB-level CHECK rejects platform_scope=true + role=ADMIN (i.e. the DTO
    validator is also enforced at the DB layer in case of direct INSERTs).
    """
    _, _, _, engine = auth_stores
    from sqlalchemy import text

    try:
        # Direct INSERT bypassing the DTO validator should fail the CHECK.
        async with engine.connect() as conn:
            with pytest.raises(Exception, match=r"role_binding_scope_triple_ck|check"):
                await conn.execute(
                    text(
                        "INSERT INTO role_binding "
                        "(id, subject_type, subject_id, tenant_id, role, platform_scope,"
                        " granted_by, granted_at) "
                        "VALUES (gen_random_uuid(), 'user', gen_random_uuid(), NULL, 'admin',"
                        " true, 'root', NOW())"
                    )
                )
                await conn.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_db_check_rejects_tenant_scope_with_system_admin_role(
    auth_stores: AuthStores,
) -> None:
    """DB-level CHECK rejects platform_scope=false + role=system_admin."""
    _, _, _, engine = auth_stores
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            with pytest.raises(Exception, match=r"role_binding_scope_triple_ck|check"):
                await conn.execute(
                    text(
                        "INSERT INTO role_binding "
                        "(id, subject_type, subject_id, tenant_id, role, platform_scope,"
                        " granted_by, granted_at) "
                        "VALUES (gen_random_uuid(), 'user', gen_random_uuid(),"
                        " gen_random_uuid(), 'system_admin', false, 'root', NOW())"
                    )
                )
                await conn.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_platform_binding_with_tenant_none(auth_stores: AuthStores) -> None:
    _, _, rb_store, engine = auth_stores
    try:
        binding = await rb_store.create(
            subject_type="user",
            subject_id=uuid4(),
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            granted_by="root",
        )
        # platform-scope delete uses tenant_id=None
        ok = await rb_store.delete(tenant_id=None, role_binding_id=binding.id)
        assert ok is True
        again = await rb_store.delete(tenant_id=None, role_binding_id=binding.id)
        assert again is False
    finally:
        await engine.dispose()
