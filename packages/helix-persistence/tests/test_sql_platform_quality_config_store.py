"""Integration test for :class:`SqlPlatformQualityConfigStore` — RT-5 (PR-3b).

Exercises migration 0119 + the single-row upsert store against a real Postgres.
Tenant-less / no RLS (like ``platform_judge_config``), so it connects with the
plain container DSN (owner) — no app-role / RLS scoping needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
from helix_agent.persistence.platform_quality_config import (
    PlatformQualityConfigRow,
    SqlPlatformQualityConfigStore,
)

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _sync_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(container: PostgresContainer) -> str:
    url = str(container.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def store(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlPlatformQualityConfigStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    yield SqlPlatformQualityConfigStore(create_async_session_factory(engine)), engine


def _row(*, enabled: bool, sampling: int) -> PlatformQualityConfigRow:
    return PlatformQualityConfigRow(
        enabled=enabled,
        sampling_rate_pct=sampling,
        daily_cap=42,
        monitor_interval_s=15,
        monitor_batch_size=7,
        judge_provider="qwen",
        judge_model="qwen3.7-max",
        drift_interval_s=20,
        drift_recent_window_h=1,
        drift_baseline_window_h=2,
        drift_min_samples=3,
        drift_threshold=0.25,
        drift_cooldown_h=6,
        updated_by="u1",
    )


@pytest.mark.asyncio
async def test_get_none_then_upsert_round_trip(
    store: tuple[SqlPlatformQualityConfigStore, AsyncEngine],
) -> None:
    quality_store, engine = store
    try:
        assert await quality_store.get() is None  # unset

        await quality_store.put(_row(enabled=True, sampling=100))
        got = await quality_store.get()
        assert got is not None
        assert got.enabled is True
        assert got.sampling_rate_pct == 100
        assert got.judge_provider == "qwen"
        assert got.drift_threshold == 0.25

        # Second put overwrites the singleton (last write wins).
        await quality_store.put(_row(enabled=False, sampling=5))
        again = await quality_store.get()
        assert again is not None
        assert again.enabled is False
        assert again.sampling_rate_pct == 5
    finally:
        await engine.dispose()
