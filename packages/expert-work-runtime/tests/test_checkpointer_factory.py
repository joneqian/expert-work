"""Tests for the checkpointer factory (memory backend + error paths).

The Postgres backend is exercised separately by ``test_checkpointer_postgres.py``
which requires testcontainers Docker.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from expert_work.runtime.checkpointer import make_checkpointer


@pytest.mark.asyncio
async def test_memory_backend_yields_in_memory_saver() -> None:
    # Stream HX-4 — the factory wraps every backend in the timing proxy.
    from expert_work.runtime.checkpointer.timing import TimingCheckpointSaver

    async with make_checkpointer("memory") as cp:
        assert isinstance(cp, TimingCheckpointSaver)
        assert isinstance(cp._inner, InMemorySaver)


@pytest.mark.asyncio
async def test_unknown_backend_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown checkpointer backend"):
        async with make_checkpointer("redis"):  # type: ignore[arg-type]
            pass


@pytest.mark.asyncio
async def test_postgres_backend_requires_dsn() -> None:
    with pytest.raises(ValueError, match="requires a non-empty dsn"):
        async with make_checkpointer("postgres"):
            pass

    with pytest.raises(ValueError, match="requires a non-empty dsn"):
        async with make_checkpointer("postgres", dsn=""):
            pass
