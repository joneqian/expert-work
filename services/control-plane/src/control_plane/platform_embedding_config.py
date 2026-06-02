"""``PlatformEmbeddingConfigService`` — Stream T (PR B).

Returns the EFFECTIVE embedding/rerank provider+model config: the runtime DB
row wins; absent a DB row, fall back to the env settings (only when the env
provider is in ``settings.effective_supported_providers``).

Mirrors :class:`PlatformSecretsService`: the resolved view is TTL-cached (one
``store.get()`` per TTL window) so the per-call resolve path doesn't hit the DB
every time; write endpoints call :meth:`invalidate` for immediate effect on the
writing instance. Multi-replica staleness is bounded by the TTL.

DB-row semantics: a row present means the admin has taken control — embedding is
``(provider, model)`` only when BOTH columns are set (else ``None``), and rerank
likewise. A row that configures embedding but leaves rerank NULL means rerank is
off; we do NOT fall back to env when a row exists.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from control_plane.settings import Settings
from helix_agent.persistence.platform_embedding_config.base import (
    PlatformEmbeddingConfigStore,
)


class PlatformEmbeddingConfigService:
    """DB-wins / env-fallback effective embedding+rerank config, TTL-cached."""

    def __init__(
        self,
        *,
        store: PlatformEmbeddingConfigStore,
        settings: Settings,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._settings = settings
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._embedding: tuple[str, str] | None = None
        self._rerank: tuple[str, str] | None = None
        self._loaded = False
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def effective_embedding_config(self) -> tuple[str, str] | None:
        """``(provider, model)`` for embeddings, or ``None`` if unconfigured."""
        await self._maybe_refresh()
        return self._embedding

    async def effective_rerank_config(self) -> tuple[str, str] | None:
        """``(provider, model)`` for rerank, or ``None`` (rerank is optional)."""
        await self._maybe_refresh()
        return self._rerank

    async def put(
        self,
        *,
        embedding_provider: str | None,
        embedding_model: str | None,
        rerank_provider: str | None,
        rerank_model: str | None,
        updated_by: str | None,
    ) -> None:
        """Upsert the singleton config row then invalidate the cache."""
        await self._store.put(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            rerank_provider=rerank_provider,
            rerank_model=rerank_model,
            updated_by=updated_by,
        )
        self.invalidate()

    def invalidate(self) -> None:
        """Drop the cache so the next read reloads from DB + env."""
        self._expires_at = 0.0

    async def _maybe_refresh(self) -> None:
        if self._loaded and self._clock() < self._expires_at:
            return
        async with self._lock:
            if self._loaded and self._clock() < self._expires_at:
                return
            await self._reload()

    async def _reload(self) -> None:
        # No ``bypass_rls_session()`` here (unlike ``PlatformSecretsService``):
        # ``platform_embedding_config`` is a tenant-less platform table with no
        # RLS policy (migration 0051), so an RLS-context wrapper is unnecessary.
        row = await self._store.get()
        if row is not None:
            self._embedding = self._pair(row.embedding_provider, row.embedding_model)
            self._rerank = self._pair(row.rerank_provider, row.rerank_model)
        else:
            self._embedding = self._env_pair(
                self._settings.embedding_provider, self._settings.embedding_model
            )
            self._rerank = self._env_pair(
                self._settings.rerank_provider, self._settings.rerank_model
            )
        self._loaded = True
        self._expires_at = self._clock() + self._ttl_seconds

    @staticmethod
    def _pair(provider: str | None, model: str | None) -> tuple[str, str] | None:
        if provider and model:
            return (provider, model)
        return None

    def _env_pair(self, provider: str, model: str) -> tuple[str, str] | None:
        if provider not in self._settings.effective_supported_providers:
            return None
        return self._pair(provider, model)
