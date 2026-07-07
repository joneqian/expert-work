import pytest

from expert_work.persistence.platform_embedding_config.memory import (
    InMemoryPlatformEmbeddingConfigStore,
)


@pytest.mark.asyncio
async def test_get_returns_none_when_unset() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    assert await store.get() is None


@pytest.mark.asyncio
async def test_put_then_get_round_trips() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        rerank_provider="qwen",
        rerank_model="qwen3-vl-rerank",
        updated_by="admin-1",
    )
    row = await store.get()
    assert row is not None
    assert row.embedding_provider == "qwen"
    assert row.embedding_model == "text-embedding-v4"
    assert row.rerank_provider == "qwen"
    assert row.rerank_model == "qwen3-vl-rerank"


@pytest.mark.asyncio
async def test_put_is_idempotent_singleton() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(
        embedding_provider="glm",
        embedding_model="embedding-3",
        rerank_provider=None,
        rerank_model=None,
        updated_by="a",
    )
    await store.put(
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        rerank_provider=None,
        rerank_model=None,
        updated_by="b",
    )
    row = await store.get()
    assert row is not None and row.embedding_provider == "qwen"  # last write wins, single row
