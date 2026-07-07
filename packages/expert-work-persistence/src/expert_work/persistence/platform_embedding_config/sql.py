"""SQLAlchemy-backed :class:`PlatformEmbeddingConfigStore` — Stream T (PR B).

Single-row singleton (``id == "singleton"``), tenant-less. Callers MUST wrap
calls in ``bypass_rls_session()`` (no RLS policy on the table). Mirrors
:class:`SqlPlatformSecretStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.models import PlatformEmbeddingConfigRow as _Model
from expert_work.persistence.platform_embedding_config.base import (
    PlatformEmbeddingConfigRow,
    PlatformEmbeddingConfigStore,
)

_SINGLETON_ID = "singleton"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _record(row: _Model) -> PlatformEmbeddingConfigRow:
    return PlatformEmbeddingConfigRow(
        embedding_provider=row.embedding_provider,
        embedding_model=row.embedding_model,
        rerank_provider=row.rerank_provider,
        rerank_model=row.rerank_model,
        updated_by=row.updated_by,
    )


class SqlPlatformEmbeddingConfigStore(PlatformEmbeddingConfigStore):
    """Postgres-backed single-row platform embedding config repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> PlatformEmbeddingConfigRow | None:
        async with self._sf() as session:
            row = (
                await session.execute(select(_Model).where(_Model.id == _SINGLETON_ID))
            ).scalar_one_or_none()
        return _record(row) if row is not None else None

    async def put(
        self,
        *,
        embedding_provider: str | None,
        embedding_model: str | None,
        rerank_provider: str | None,
        rerank_model: str | None,
        updated_by: str | None,
    ) -> None:
        now = _utc_now()
        async with self._sf() as session:
            stmt = (
                pg_insert(_Model)
                .values(
                    id=_SINGLETON_ID,
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                    rerank_provider=rerank_provider,
                    rerank_model=rerank_model,
                    updated_at=now,
                    updated_by=updated_by,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "embedding_provider": embedding_provider,
                        "embedding_model": embedding_model,
                        "rerank_provider": rerank_provider,
                        "rerank_model": rerank_model,
                        "updated_at": now,
                        "updated_by": updated_by,
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()
