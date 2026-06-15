"""SQLAlchemy-backed :class:`PlatformJudgeConfigStore` — Stream PI-3-A1.

Single-row singleton (``id == "singleton"``), tenant-less. Callers MUST wrap
calls in ``bypass_rls_session()`` (no RLS policy on the table). Mirrors
:class:`SqlPlatformEmbeddingConfigStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import PlatformJudgeConfigRow as _Model
from helix_agent.persistence.platform_judge_config.base import (
    PlatformJudgeConfigRow,
    PlatformJudgeConfigStore,
)

_SINGLETON_ID = "singleton"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _record(row: _Model) -> PlatformJudgeConfigRow:
    return PlatformJudgeConfigRow(
        judge_provider=row.judge_provider,
        judge_model=row.judge_model,
        updated_by=row.updated_by,
    )


class SqlPlatformJudgeConfigStore(PlatformJudgeConfigStore):
    """Postgres-backed single-row platform judge config repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> PlatformJudgeConfigRow | None:
        async with self._sf() as session:
            row = (
                await session.execute(select(_Model).where(_Model.id == _SINGLETON_ID))
            ).scalar_one_or_none()
        return _record(row) if row is not None else None

    async def put(
        self,
        *,
        judge_provider: str | None,
        judge_model: str | None,
        updated_by: str | None,
    ) -> None:
        now = _utc_now()
        async with self._sf() as session:
            stmt = (
                pg_insert(_Model)
                .values(
                    id=_SINGLETON_ID,
                    judge_provider=judge_provider,
                    judge_model=judge_model,
                    updated_at=now,
                    updated_by=updated_by,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "judge_provider": judge_provider,
                        "judge_model": judge_model,
                        "updated_at": now,
                        "updated_by": updated_by,
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()
