# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/checkpointer/{provider,async_provider}.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Async-only (we are an async stack throughout); sync path dropped
#   - SQLite backend dropped (Postgres-only per ADR-0004)
#   - No module-level singleton (DeerFlow's global _checkpointer + _checkpointer_ctx);
#     dependency injection via FastAPI lifespan or explicit context manager
#   - No DeerFlow config-system coupling; backend + DSN passed explicitly
# Last sync: 2026-05-11
# ============================================================

"""Factory for ``langgraph.types.Checkpointer`` instances.

Two backends:

- ``memory`` — ``langgraph.checkpoint.memory.InMemorySaver`` (tests, dev)
- ``postgres`` — ``langgraph.checkpoint.postgres.aio.AsyncPostgresSaver``

Usage in FastAPI lifespan::

    from expert_work.runtime.checkpointer import make_checkpointer

    async with make_checkpointer("postgres", dsn) as checkpointer:
        app.state.checkpointer = checkpointer
        yield
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)

CheckpointerBackend = Literal["memory", "postgres"]

# Replicas that start at the same time (dev compose brings the blue + green
# control-plane pair up together; a rolling deploy can briefly overlap them)
# all call ``AsyncPostgresSaver.setup()`` at once. LangGraph's first-run setup
# is NOT concurrency-safe — two ``CREATE TYPE`` race and the loser fails with
# ``duplicate key value violates unique constraint "pg_type_typname_nsp_index"``
# (or a transient ``DeadlockDetected``). setup() is idempotent *across* runs,
# though: once the winner has created + recorded a migration, a re-run skips it.
# So we simply retry on those concurrency errors until the loser sees the
# winner's objects already present. Bounded; re-raises anything else / on exhaust.
_SETUP_MAX_ATTEMPTS = 8
_SETUP_RETRY_BASE_DELAY_S = 0.1


async def _setup_with_retry(saver: Any) -> None:
    """Run ``saver.setup()``, retrying transient concurrent-DDL collisions.

    Two replicas calling first-run setup at once race ``CREATE TYPE`` /
    ``CREATE TABLE``; the loser raises a uniqueness/duplicate or deadlock
    error. setup() is idempotent across runs, so the loser just retries until
    it observes the winner's objects. Any other error (or running out of
    attempts) propagates.
    """
    import asyncio

    import psycopg.errors

    transient = (
        psycopg.errors.UniqueViolation,
        psycopg.errors.DuplicateObject,
        psycopg.errors.DuplicateTable,
        psycopg.errors.DeadlockDetected,
    )
    for attempt in range(_SETUP_MAX_ATTEMPTS):
        try:
            await saver.setup()
            return
        except transient:
            if attempt == _SETUP_MAX_ATTEMPTS - 1:
                raise
            logger.info("checkpointer.postgres.setup_retry attempt=%d", attempt + 1)
            await asyncio.sleep(_SETUP_RETRY_BASE_DELAY_S * (attempt + 1))


@contextlib.asynccontextmanager
async def make_checkpointer(
    backend: CheckpointerBackend,
    dsn: str | None = None,
) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """Yield a configured async LangGraph checkpointer; tear down on exit.

    :param backend: ``"memory"`` (tests / dev) or ``"postgres"`` (prod)
    :param dsn: ``postgresql://...`` connection string. Required for postgres.
                Use the **sync driver scheme** (``postgresql://`` or
                ``postgresql+psycopg://``) — ``AsyncPostgresSaver`` manages
                its own async pool under the hood.
    :raises ValueError: backend unknown or postgres DSN missing
    """
    # Widen to ``str`` so the trailing "unknown backend" path is reachable to
    # both mypy and runtime (type-erased callers, e.g. config strings, are
    # the typical source of bad values).
    # Stream HX-4 (Mini-ADR HX-D3) — both backends leave the factory
    # wrapped in the timing proxy so the IO histogram exists on every
    # deployment shape (and tests exercise the same call path as prod).
    from expert_work.runtime.checkpointer.timing import TimingCheckpointSaver

    bk: str = backend
    if bk == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("checkpointer.memory.init")
        yield TimingCheckpointSaver(InMemorySaver())
        return

    if bk == "postgres":
        if not dsn:
            msg = "checkpointer backend 'postgres' requires a non-empty dsn"
            raise ValueError(msg)

        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            await _setup_with_retry(saver)
            logger.info("checkpointer.postgres.ready")
            yield TimingCheckpointSaver(saver)
        return

    msg = f"unknown checkpointer backend: {bk!r}"
    raise ValueError(msg)
