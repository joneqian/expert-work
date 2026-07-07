"""Integration tests for the SQL platform agent template store — Agent-Templates M1.

The table is platform (NULL-tenant). Under the ``IS NOT DISTINCT FROM`` RLS
policy, a session with no ``app.tenant_id`` set can read/write the NULL-tenant
rows — the path the control-plane uses via ``bypass_rls_session()``. These CRUD
tests run on the unprivileged app role with the tenant context unset.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from expert_work.persistence import (
    DatabaseConfig,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.persistence.platform_agent_template import (
    PlatformAgentTemplateAlreadyExistsError,
    PlatformAgentTemplateNotFoundError,
    SqlPlatformAgentTemplateStore,
)
from expert_work.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from expert_work.protocol import (
    AgentSpec,
    PlatformAgentTemplatePatch,
    PlatformAgentTemplateStatus,
    PlatformAgentTemplateUpsert,
    TenantPlan,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

APP_ROLE = "expert_work_app"
APP_PASSWORD = "expert_work_app_test_pw"  # test-only fixture password

_BASE_SPEC: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "support-bot", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are a support agent"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


def _rewrite_credentials(dsn: str, user: str, password: str) -> str:
    parsed = urlparse(dsn)
    new_netloc = f"{user}:{password}@{parsed.hostname}"
    if parsed.port is not None:
        new_netloc = f"{new_netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=new_netloc))


def _provision_app_role(sync_dsn: str) -> None:
    admin_engine = create_engine(sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
                {"role": APP_ROLE},
            ).first()
            if exists is None:
                conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
            conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE "
                    f"ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
                )
            )
            conn.execute(
                text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
            )
    finally:
        admin_engine.dispose()


@pytest.fixture
def template_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlPlatformAgentTemplateStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlPlatformAgentTemplateStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    # NULL tenant context = the platform read/write path for this table.
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


def _slug() -> str:
    return f"tmpl-{uuid4().hex[:12]}"


def _upsert(
    *, name: str | None = None, version: str = "1.0.0", **over: Any
) -> PlatformAgentTemplateUpsert:
    doc = deepcopy(_BASE_SPEC)
    doc["metadata"]["name"] = name or _slug()
    doc["metadata"]["version"] = version
    kwargs: dict[str, Any] = {
        "spec": AgentSpec.model_validate(doc),
        "display_name": "Support Bot",
        "category": "support",
        "status": PlatformAgentTemplateStatus.PUBLISHED,
        "required_tier": TenantPlan.PRO,
    }
    kwargs.update(over)
    return PlatformAgentTemplateUpsert(**kwargs)


@pytest.mark.asyncio
async def test_create_get_round_trip(
    template_store: tuple[SqlPlatformAgentTemplateStore, AsyncEngine],
) -> None:
    store, engine = template_store
    try:
        up = _upsert()
        created = await store.create(upsert=up, created_by="sysadmin")
        assert created.tenant_id is None
        assert created.required_tier is TenantPlan.PRO
        assert created.name == up.spec.metadata.name
        assert len(created.spec_sha256) == 64
        got = await store.get(name=created.name, version="1.0.0")
        assert got is not None and got.id == created.id
        # AgentSpec JSONB round-trips.
        assert got.spec.spec.system_prompt.template == "you are a support agent"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_name_version_rejected(
    template_store: tuple[SqlPlatformAgentTemplateStore, AsyncEngine],
) -> None:
    store, engine = template_store
    try:
        name = _slug()
        await store.create(upsert=_upsert(name=name), created_by="s")
        with pytest.raises(PlatformAgentTemplateAlreadyExistsError):
            await store.create(upsert=_upsert(name=name), created_by="s")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_versions_coexist_and_latest(
    template_store: tuple[SqlPlatformAgentTemplateStore, AsyncEngine],
) -> None:
    store, engine = template_store
    try:
        name = _slug()
        await store.create(upsert=_upsert(name=name, version="1.0.0"), created_by="s")
        await store.create(upsert=_upsert(name=name, version="2.0.0"), created_by="s")
        versions = await store.list_versions(name=name)
        assert {r.version for r in versions} == {"1.0.0", "2.0.0"}
        latest = await store.get_latest(name=name)
        assert latest is not None and latest.version == "2.0.0"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_spec_and_meta(
    template_store: tuple[SqlPlatformAgentTemplateStore, AsyncEngine],
) -> None:
    store, engine = template_store
    try:
        name = _slug()
        created = await store.create(upsert=_upsert(name=name), created_by="s")
        new_doc = deepcopy(_BASE_SPEC)
        new_doc["metadata"]["name"] = name
        new_doc["spec"]["system_prompt"]["template"] = "fixed prompt"
        updated = await store.update_spec(
            name=name, version="1.0.0", spec=AgentSpec.model_validate(new_doc), updated_by="bob"
        )
        assert updated is not None
        assert updated.spec.spec.system_prompt.template == "fixed prompt"
        assert updated.spec_sha256 != created.spec_sha256

        patched = await store.update_meta(
            name=name,
            version="1.0.0",
            patch=PlatformAgentTemplatePatch(status=PlatformAgentTemplateStatus.DRAFT),
        )
        assert patched is not None and patched.status is PlatformAgentTemplateStatus.DRAFT
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete(
    template_store: tuple[SqlPlatformAgentTemplateStore, AsyncEngine],
) -> None:
    store, engine = template_store
    try:
        name = _slug()
        await store.create(upsert=_upsert(name=name), created_by="s")
        await store.delete(name=name, version="1.0.0")
        assert await store.get(name=name, version="1.0.0") is None
        with pytest.raises(PlatformAgentTemplateNotFoundError):
            await store.delete(name=name, version="1.0.0")
    finally:
        await engine.dispose()
