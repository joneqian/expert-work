"""Async engine + session factory wiring.

Stream A.3 adds PgBouncer transaction-mode support. When the app talks to
PgBouncer (the common path; see ``infra/docker-compose.yml``) we must:

1. Disable SQLAlchemy connection pooling (use ``NullPool``) — PgBouncer
   is already pooling on the server side; double-pooling pins client
   connections to backend slots and defeats the point of transaction mode.
2. Disable asyncpg's per-connection prepared-statement cache
   (``statement_cache_size=0``) — under transaction mode the same
   connection may be reused by different sessions between transactions,
   so cached prepared statements aren't valid.

Design: subsystems/23-postgres-scalability.md § 5.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession as _AsyncSession,
)
from sqlalchemy.pool import NullPool


@dataclass(frozen=True)
class DatabaseConfig:
    """Postgres connection config consumed by :func:`create_async_engine_from_config`.

    Two operating modes:

    - **Direct Postgres** (``pgbouncer_mode=False``): SQLAlchemy manages a
      QueuePool of ``pool_size + max_overflow`` connections. Use for
      migrations, admin scripts, and tests that need session-state features
      (advisory locks across statements, ``LISTEN``/``NOTIFY``, etc.).

    - **PgBouncer transaction mode** (``pgbouncer_mode=True``): SQLAlchemy
      uses ``NullPool`` and asyncpg's prepared-statement cache is disabled.
      Use for application traffic (the common path).

    ``pool_size``, ``max_overflow``, and ``pool_timeout_s`` are ignored when
    ``pgbouncer_mode=True``; PgBouncer's ``default_pool_size`` is what
    matters.
    """

    dsn: str
    pgbouncer_mode: bool = False
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout_s: float = 30.0
    pool_pre_ping: bool = True
    echo_sql: bool = False
    connect_args: dict[str, Any] = field(default_factory=dict)


def build_engine_kwargs(config: DatabaseConfig) -> dict[str, Any]:
    """Compute the kwargs that :func:`create_async_engine_from_config`
    passes to :func:`sqlalchemy.ext.asyncio.create_async_engine`.

    Exposed for unit tests — SQLAlchemy does not surface user-supplied
    ``connect_args`` through engine inspection APIs, so the safest way to
    verify PgBouncer wiring is to inspect this dict directly.
    """
    connect_args: dict[str, Any] = dict(config.connect_args)
    kwargs: dict[str, Any] = {
        "echo": config.echo_sql,
        "future": True,
        "pool_pre_ping": config.pool_pre_ping,
    }

    if config.pgbouncer_mode:
        # Disable SQLAlchemy pooling (PgBouncer pools server-side) and
        # asyncpg's prepared-statement cache (incompatible with txn mode).
        kwargs["poolclass"] = NullPool
        connect_args.setdefault("statement_cache_size", 0)
        connect_args.setdefault("prepared_statement_cache_size", 0)
    else:
        kwargs["pool_size"] = config.pool_size
        kwargs["max_overflow"] = config.max_overflow
        kwargs["pool_timeout"] = config.pool_timeout_s

    if connect_args:
        kwargs["connect_args"] = connect_args

    return kwargs


def create_async_engine_from_config(config: DatabaseConfig) -> AsyncEngine:
    """Build an asyncpg-backed engine.

    DSN must use ``postgresql+asyncpg://`` driver scheme.
    """
    return create_async_engine(config.dsn, **build_engine_kwargs(config))


def create_async_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[_AsyncSession]:
    """Build an ``AsyncSession`` factory with no autocommit / no autoflush.

    Caller manages transactions explicitly via ``async with session.begin()``.
    """
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
