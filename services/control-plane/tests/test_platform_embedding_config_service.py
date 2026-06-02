import pytest

from control_plane.platform_embedding_config import PlatformEmbeddingConfigService
from helix_agent.persistence.platform_embedding_config.memory import (
    InMemoryPlatformEmbeddingConfigStore,
)


class _Settings:
    embedding_provider = "qwen"
    embedding_model = "text-embedding-v4"
    rerank_provider = "qwen"
    rerank_model = "qwen3-vl-rerank"
    effective_supported_providers = ("qwen", "openai")


@pytest.mark.asyncio
async def test_falls_back_to_env_when_no_db_row() -> None:
    svc = PlatformEmbeddingConfigService(
        store=InMemoryPlatformEmbeddingConfigStore(), settings=_Settings()
    )
    assert await svc.effective_embedding_config() == ("qwen", "text-embedding-v4")
    assert await svc.effective_rerank_config() == ("qwen", "qwen3-vl-rerank")


@pytest.mark.asyncio
async def test_db_row_wins_over_env_and_rerank_off_when_null() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(
        embedding_provider="glm",
        embedding_model="embedding-3",
        rerank_provider=None,
        rerank_model=None,
        updated_by="a",
    )
    svc = PlatformEmbeddingConfigService(store=store, settings=_Settings())
    assert await svc.effective_embedding_config() == ("glm", "embedding-3")
    assert await svc.effective_rerank_config() is None


@pytest.mark.asyncio
async def test_env_fallback_none_when_provider_unsupported() -> None:
    class S(_Settings):
        embedding_provider = "doubao"  # not in supported

    svc = PlatformEmbeddingConfigService(store=InMemoryPlatformEmbeddingConfigStore(), settings=S())
    assert await svc.effective_embedding_config() is None


@pytest.mark.asyncio
async def test_cache_then_invalidate_picks_up_new_row() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    svc = PlatformEmbeddingConfigService(store=store, settings=_Settings())
    assert await svc.effective_embedding_config() == ("qwen", "text-embedding-v4")  # env
    await store.put(
        embedding_provider="glm",
        embedding_model="embedding-3",
        rerank_provider=None,
        rerank_model=None,
        updated_by="a",
    )
    # still cached (env) until invalidate
    assert await svc.effective_embedding_config() == ("qwen", "text-embedding-v4")
    svc.invalidate()
    assert await svc.effective_embedding_config() == ("glm", "embedding-3")


@pytest.mark.asyncio
async def test_put_writes_and_invalidates() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    svc = PlatformEmbeddingConfigService(store=store, settings=_Settings())
    assert await svc.effective_embedding_config() == ("qwen", "text-embedding-v4")  # env, cached
    await svc.put(
        embedding_provider="glm",
        embedding_model="embedding-3",
        rerank_provider=None,
        rerank_model=None,
        updated_by="admin-1",
    )
    assert await svc.effective_embedding_config() == ("glm", "embedding-3")
    assert await svc.effective_rerank_config() is None
