import pytest

from expert_work.persistence.platform_billing_config.memory import (
    InMemoryPlatformBillingConfigStore,
)


@pytest.mark.asyncio
async def test_get_returns_none_when_unset() -> None:
    store = InMemoryPlatformBillingConfigStore()
    assert await store.get() is None


@pytest.mark.asyncio
async def test_put_then_get_round_trips() -> None:
    store = InMemoryPlatformBillingConfigStore()
    await store.put(rollup_enabled=False, updated_by="admin-1")
    row = await store.get()
    assert row is not None
    assert row.rollup_enabled is False
    assert row.updated_by == "admin-1"


@pytest.mark.asyncio
async def test_put_is_idempotent_singleton() -> None:
    store = InMemoryPlatformBillingConfigStore()
    await store.put(rollup_enabled=False, updated_by="a")
    await store.put(rollup_enabled=True, updated_by="b")
    row = await store.get()
    assert row is not None and row.rollup_enabled is True  # last write wins, single row
