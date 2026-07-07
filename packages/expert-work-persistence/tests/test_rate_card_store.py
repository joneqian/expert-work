"""Unit tests for the in-memory model rate-card store — 模型定价简化.

Covers CRUD + conflict + the ``resolve`` single-row lookup. The SQL store shares
the pure ``_resolve`` helper, so resolution semantics are exercised here against
the in-memory impl; SQL CRUD/RLS lives in the integration suite.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from expert_work.persistence.billing import (
    InMemoryModelRateCardStore,
    ModelRateCardConflictError,
    ModelRateCardNotFoundError,
)
from expert_work.protocol import (
    ModelRateCardPatch,
    ModelRateCardRecord,
    ModelRateCardUpsert,
)


async def _make(store: InMemoryModelRateCardStore, **over: Any) -> ModelRateCardRecord:
    kwargs: dict[str, Any] = {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "input_per_mtok_micros": 15_000_000,
        "output_per_mtok_micros": 75_000_000,
    }
    kwargs.update(over)
    return await store.create(upsert=ModelRateCardUpsert(**kwargs), actor_id="sysadmin")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get() -> None:
    store = InMemoryModelRateCardStore()
    rec = await _make(store)
    assert rec.tenant_id is None
    fetched = await store.get(rec.id)
    assert fetched is not None
    assert fetched.input_per_mtok_micros == 15_000_000
    assert fetched.cache_creation_per_mtok_micros == 0


@pytest.mark.asyncio
async def test_get_missing_returns_none() -> None:
    store = InMemoryModelRateCardStore()
    assert await store.get(uuid4()) is None


@pytest.mark.asyncio
async def test_create_duplicate_conflicts() -> None:
    store = InMemoryModelRateCardStore()
    await _make(store)
    # One price per (provider, model) — a second create collides.
    with pytest.raises(ModelRateCardConflictError):
        await _make(store)


@pytest.mark.asyncio
async def test_create_different_model_ok() -> None:
    store = InMemoryModelRateCardStore()
    await _make(store)
    rec = await _make(store, model="claude-sonnet-4-6")
    assert rec.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_patch_updates_price() -> None:
    store = InMemoryModelRateCardStore()
    rec = await _make(store)
    updated = await store.patch(
        rate_card_id=rec.id, patch=ModelRateCardPatch(input_per_mtok_micros=99_000_000)
    )
    assert updated.input_per_mtok_micros == 99_000_000
    # output untouched
    assert updated.output_per_mtok_micros == 75_000_000


@pytest.mark.asyncio
async def test_patch_missing_raises() -> None:
    store = InMemoryModelRateCardStore()
    with pytest.raises(ModelRateCardNotFoundError):
        await store.patch(rate_card_id=uuid4(), patch=ModelRateCardPatch(input_per_mtok_micros=1))


@pytest.mark.asyncio
async def test_delete() -> None:
    store = InMemoryModelRateCardStore()
    rec = await _make(store)
    await store.delete(rec.id)
    assert await store.get(rec.id) is None


@pytest.mark.asyncio
async def test_delete_missing_raises() -> None:
    store = InMemoryModelRateCardStore()
    with pytest.raises(ModelRateCardNotFoundError):
        await store.delete(uuid4())


# ---------------------------------------------------------------------------
# list filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_provider_model() -> None:
    store = InMemoryModelRateCardStore()
    await _make(store)
    await _make(store, provider="openai", model="gpt-5.5")
    assert len(await store.list()) == 2
    only_oai = await store.list(provider="openai")
    assert len(only_oai) == 1
    assert only_oai[0].provider == "openai"
    only_model = await store.list(model="claude-opus-4-8")
    assert len(only_model) == 1


# ---------------------------------------------------------------------------
# resolve — single row per (provider, model)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_hit() -> None:
    store = InMemoryModelRateCardStore()
    rec = await _make(store)
    hit = await store.resolve(provider="anthropic", model="claude-opus-4-8")
    assert hit is not None and hit.id == rec.id
    assert hit.input_per_mtok_micros == 15_000_000


@pytest.mark.asyncio
async def test_resolve_no_match_returns_none() -> None:
    store = InMemoryModelRateCardStore()
    await _make(store)
    assert await store.resolve(provider="openai", model="gpt-5.5") is None
