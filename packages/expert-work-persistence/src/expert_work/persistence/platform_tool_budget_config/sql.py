"""SQLAlchemy-backed :class:`PlatformToolBudgetConfigStore` — Phase 3.

Single-row singleton (``id == "singleton"``), tenant-less. Callers MUST wrap
calls in ``bypass_rls_session()`` (no RLS policy on the table). Mirrors
:class:`SqlPlatformJudgeConfigStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.models import PlatformToolBudgetConfigRow as _Model
from expert_work.persistence.platform_tool_budget_config.base import (
    PlatformToolBudgetConfigRow,
    PlatformToolBudgetConfigStore,
)

_SINGLETON_ID = "singleton"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _record(row: _Model) -> PlatformToolBudgetConfigRow:
    return PlatformToolBudgetConfigRow(enabled=row.enabled, updated_by=row.updated_by)


class SqlPlatformToolBudgetConfigStore(PlatformToolBudgetConfigStore):
    """Postgres-backed single-row platform tool-budget config repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self) -> PlatformToolBudgetConfigRow | None:
        async with self._sf() as session:
            row = (
                await session.execute(select(_Model).where(_Model.id == _SINGLETON_ID))
            ).scalar_one_or_none()
        return _record(row) if row is not None else None

    async def put(self, *, enabled: bool, updated_by: str | None) -> None:
        now = _utc_now()
        async with self._sf() as session:
            stmt = (
                pg_insert(_Model)
                .values(
                    id=_SINGLETON_ID,
                    enabled=enabled,
                    updated_at=now,
                    updated_by=updated_by,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"enabled": enabled, "updated_at": now, "updated_by": updated_by},
                )
            )
            await session.execute(stmt)
            await session.commit()
