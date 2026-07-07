"""Abstract :class:`PlatformEmbeddingConfigStore` — Stream T (PR B).

Single-row singleton storing the platform's chosen embedding / rerank
provider+model selection (non-secret). Tenant-less (platform-global), so SQL
callers MUST be inside ``bypass_rls_session()`` — there is no per-tenant RLS
scope to satisfy, exactly like ``platform_provider_secret``.

An absent row means "not configured". Mirrors the ``platform_secret``
machinery (store split base/memory/sql).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformEmbeddingConfigRow:
    """The platform's embedding / rerank selection (non-secret)."""

    embedding_provider: str | None
    embedding_model: str | None
    rerank_provider: str | None
    rerank_model: str | None
    updated_by: str | None


class PlatformEmbeddingConfigStore(abc.ABC):
    """Persistence Protocol for the single-row platform embedding config."""

    @abc.abstractmethod
    async def get(self) -> PlatformEmbeddingConfigRow | None:
        """The singleton row, or None if not configured. SQL callers bypass RLS."""

    @abc.abstractmethod
    async def put(
        self,
        *,
        embedding_provider: str | None,
        embedding_model: str | None,
        rerank_provider: str | None,
        rerank_model: str | None,
        updated_by: str | None,
    ) -> None:
        """Upsert the singleton row (last write wins). SQL callers bypass RLS."""
