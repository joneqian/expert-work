import pytest

from helix_agent.persistence.platform_tool_budget_config.memory import (
    InMemoryPlatformToolBudgetConfigStore,
)


@pytest.mark.asyncio
async def test_get_returns_none_when_unset() -> None:
    store = InMemoryPlatformToolBudgetConfigStore()
    assert await store.get() is None


@pytest.mark.asyncio
async def test_put_then_get_round_trips() -> None:
    store = InMemoryPlatformToolBudgetConfigStore()
    await store.put(enabled=False, updated_by="admin-1")
    row = await store.get()
    assert row is not None
    assert row.enabled is False
    assert row.updated_by == "admin-1"


@pytest.mark.asyncio
async def test_put_is_idempotent_singleton() -> None:
    store = InMemoryPlatformToolBudgetConfigStore()
    await store.put(enabled=False, updated_by="a")
    await store.put(enabled=True, updated_by="b")
    row = await store.get()
    assert row is not None and row.enabled is True  # last write wins, single row
