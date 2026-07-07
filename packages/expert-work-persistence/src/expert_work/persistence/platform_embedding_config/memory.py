"""In-memory :class:`PlatformEmbeddingConfigStore` — Stream T (PR B)."""

from __future__ import annotations

import asyncio

from expert_work.persistence.platform_embedding_config.base import (
    PlatformEmbeddingConfigRow,
    PlatformEmbeddingConfigStore,
)


class InMemoryPlatformEmbeddingConfigStore(PlatformEmbeddingConfigStore):
    """Holds a single optional row; lock-guarded for asyncio safety."""

    def __init__(self) -> None:
        self._row: PlatformEmbeddingConfigRow | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> PlatformEmbeddingConfigRow | None:
        async with self._lock:
            return self._row

    async def put(
        self,
        *,
        embedding_provider: str | None,
        embedding_model: str | None,
        rerank_provider: str | None,
        rerank_model: str | None,
        updated_by: str | None,
    ) -> None:
        async with self._lock:
            self._row = PlatformEmbeddingConfigRow(
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                rerank_provider=rerank_provider,
                rerank_model=rerank_model,
                updated_by=updated_by,
            )
