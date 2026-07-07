"""Tests for the store factory (memory backend + error paths)."""

from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from expert_work.runtime.store import make_store


@pytest.mark.asyncio
async def test_memory_backend_yields_in_memory_store() -> None:
    async with make_store("memory") as store:
        assert isinstance(store, InMemoryStore)


@pytest.mark.asyncio
async def test_unknown_backend_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown store backend"):
        async with make_store("redis"):  # type: ignore[arg-type]
            pass


@pytest.mark.asyncio
async def test_postgres_backend_requires_dsn() -> None:
    with pytest.raises(ValueError, match="requires a non-empty dsn"):
        async with make_store("postgres"):
            pass
