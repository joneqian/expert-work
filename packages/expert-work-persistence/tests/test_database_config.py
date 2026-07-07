"""Unit tests for :func:`create_async_engine_from_config` / ``build_engine_kwargs``.

We don't open a connection here — just inspect the engine builder output.
PgBouncer-fronted Postgres is exercised end-to-end in
:mod:`test_pgbouncer_integration`.
"""

from __future__ import annotations

from sqlalchemy.pool import NullPool, QueuePool

from expert_work.persistence.database import (
    DatabaseConfig,
    build_engine_kwargs,
    create_async_engine_from_config,
)

# Driver name only — never opened; we just inspect engine config.
_DSN = "postgresql+asyncpg://u:p@localhost:5432/db"


def test_direct_mode_uses_queue_pool() -> None:
    config = DatabaseConfig(dsn=_DSN, pgbouncer_mode=False, pool_size=7, max_overflow=3)
    engine = create_async_engine_from_config(config)

    sync_engine = engine.sync_engine
    assert isinstance(sync_engine.pool, QueuePool)
    assert sync_engine.pool.size() == 7
    assert sync_engine.pool._max_overflow == 3


def test_direct_mode_kwargs_include_pool_sizing() -> None:
    kwargs = build_engine_kwargs(
        DatabaseConfig(dsn=_DSN, pool_size=5, max_overflow=10, pool_timeout_s=15.0)
    )
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 10
    assert kwargs["pool_timeout"] == 15.0
    assert "poolclass" not in kwargs
    assert "connect_args" not in kwargs


def test_pgbouncer_mode_uses_null_pool() -> None:
    engine = create_async_engine_from_config(DatabaseConfig(dsn=_DSN, pgbouncer_mode=True))
    assert isinstance(engine.sync_engine.pool, NullPool)


def test_pgbouncer_mode_disables_asyncpg_statement_cache() -> None:
    """Under PgBouncer transaction mode asyncpg's prepared-statement cache
    must be zero — same backend connection may be reused across sessions
    between transactions, invalidating cached statements. Regression here
    silently breaks prod, so the assertion lives close to the config."""
    kwargs = build_engine_kwargs(DatabaseConfig(dsn=_DSN, pgbouncer_mode=True))
    assert kwargs["poolclass"] is NullPool
    assert "pool_size" not in kwargs  # mutually exclusive with NullPool
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    assert kwargs["connect_args"]["prepared_statement_cache_size"] == 0


def test_pgbouncer_mode_respects_explicit_connect_args() -> None:
    """Caller-supplied connect_args win over our defaults so callers can
    raise the cache or thread custom asyncpg kwargs through."""
    kwargs = build_engine_kwargs(
        DatabaseConfig(
            dsn=_DSN,
            pgbouncer_mode=True,
            connect_args={"statement_cache_size": 5, "server_settings": {"app": "x"}},
        )
    )
    assert kwargs["connect_args"]["statement_cache_size"] == 5
    assert kwargs["connect_args"]["server_settings"] == {"app": "x"}
    # We still inject the prepared_statement_cache_size default since the
    # caller didn't override it.
    assert kwargs["connect_args"]["prepared_statement_cache_size"] == 0


def test_pool_pre_ping_default_true() -> None:
    engine = create_async_engine_from_config(DatabaseConfig(dsn=_DSN))
    assert engine.sync_engine.pool._pre_ping is True


def test_pool_pre_ping_opt_out() -> None:
    kwargs = build_engine_kwargs(DatabaseConfig(dsn=_DSN, pool_pre_ping=False))
    assert kwargs["pool_pre_ping"] is False
