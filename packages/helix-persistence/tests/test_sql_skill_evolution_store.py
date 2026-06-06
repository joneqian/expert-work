"""Integration: SkillStore evolution API on real Postgres — Stream SE (SE-2).

Pins on a real PG (migration 0065 applied) that:

* the ownership/lineage columns (``visibility`` / ``created_by_agent_id`` /
  ``forked_from``) and evolution-provenance columns
  (``evolution_origin`` / ``distilled_from_*`` / ``evolution_round``)
  round-trip through the SQL store;
* ``fork_skill`` copies the source's latest version into a new
  ``agent_private`` skill;
* ``skill_eval_result`` rows round-trip and are tenant-isolated by RLS
  (the SE-A2 evidence table must not leak across tenants).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from helix_agent.persistence import (
    DatabaseConfig,
    SqlSkillStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from helix_agent.protocol import SkillEvalResult

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
APP_ROLE = "helix_app"
APP_PASSWORD = "helix_app_test_pw"  # test-only fixture password


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
def skill_store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlSkillStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    _provision_app_role(_sync_dsn(postgres_container))

    app_dsn = _rewrite_credentials(_async_dsn(postgres_container), APP_ROLE, APP_PASSWORD)
    engine = create_async_engine_from_config(DatabaseConfig(dsn=app_dsn))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlSkillStore(sf), engine


@pytest.fixture(autouse=True)
def reset_rls() -> Iterator[None]:
    tok = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tok)


@pytest.mark.asyncio
async def test_evolution_columns_round_trip(
    skill_store: tuple[SqlSkillStore, AsyncEngine],
) -> None:
    store, engine = skill_store
    tenant, agent_id, src, cand = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        current_tenant_id_var.set(tenant)
        sid = uuid4()
        await store.create_skill(
            skill_id=sid,
            tenant_id=tenant,
            name=f"agent-{uuid4().hex[:8]}",
            visibility="agent_private",
            created_by_agent_id=agent_id,
            forked_from=src,
        )
        got = await store.get_skill(skill_id=sid, tenant_id=tenant)
        assert got is not None
        assert got.visibility == "agent_private"
        assert got.created_by_agent_id == agent_id
        assert got.forked_from == src

        v = await store.add_version(
            version_id=uuid4(),
            skill_id=sid,
            tenant_id=tenant,
            prompt_fragment="distilled body",
            authored_by="agent",
            evolution_origin="distilled",
            distilled_from_trajectory_key="t/abc.jsonl",
            distilled_from_candidate_id=cand,
            evolution_round=2,
        )
        assert v.evolution_origin == "distilled"
        assert v.distilled_from_candidate_id == cand
        assert v.evolution_round == 2
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_fork_skill_real_pg(
    skill_store: tuple[SqlSkillStore, AsyncEngine],
) -> None:
    store, engine = skill_store
    tenant, agent_id = uuid4(), uuid4()
    try:
        current_tenant_id_var.set(tenant)
        src = uuid4()
        await store.create_skill(skill_id=src, tenant_id=tenant, name=f"src-{uuid4().hex[:8]}")
        await store.add_version(
            version_id=uuid4(),
            skill_id=src,
            tenant_id=tenant,
            prompt_fragment="source body",
            tool_names=("web_search",),
        )
        new_sid, new_vid = uuid4(), uuid4()
        forked = await store.fork_skill(
            tenant_id=tenant,
            source_skill_id=src,
            new_name=f"fork-{uuid4().hex[:8]}",
            by_agent_id=agent_id,
            new_skill_id=new_sid,
            new_version_id=new_vid,
        )
        assert forked.visibility == "agent_private"
        assert forked.forked_from == src
        assert forked.latest_version == 1
        v = await store.get_version_by_number(skill_id=new_sid, tenant_id=tenant, version=1)
        assert v is not None and v.prompt_fragment == "source body"
        assert v.tool_names == ("web_search",)
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_eval_result_round_trip_and_tenant_isolation(
    skill_store: tuple[SqlSkillStore, AsyncEngine],
) -> None:
    store, engine = skill_store
    tenant_a, tenant_b = uuid4(), uuid4()
    skill_a = uuid4()
    try:
        current_tenant_id_var.set(tenant_a)
        await store.record_eval_result(
            result=SkillEvalResult(
                id=uuid4(),
                tenant_id=tenant_a,
                skill_id=skill_a,
                skill_version=1,
                baseline_score=0.4,
                skill_score=0.85,
                delta=0.45,
                n_cases=12,
                replay_source="trajectory",
                verdict="pass",
                created_at=datetime.now(UTC),
            )
        )
        rows = await store.list_eval_results(skill_id=skill_a, tenant_id=tenant_a)
        assert len(rows) == 1
        assert rows[0].verdict == "pass"
        assert rows[0].delta == pytest.approx(0.45)

        # RLS: tenant B sees nothing for tenant A's skill.
        current_tenant_id_var.set(tenant_b)
        leaked = await store.list_eval_results(skill_id=skill_a, tenant_id=tenant_b)
        assert leaked == []
    finally:
        current_tenant_id_var.set(None)
        await engine.dispose()
