"""Sampling candidate feed — Stream RT-5 (RT-ADR-22).

Read-only source of "runs that just finished successfully" for the quality
monitor's pull sampler. A candidate carries everything the judge needs
without a second query: run + tenant + thread ids, plus the agent identity
(``agent_name`` / ``agent_version``) which lives on ``thread_meta`` — the
``agent_run`` row has neither.

The scan is cross-tenant (the caller wraps it in the RLS-bypass scope, same
as the transcript-mirror sweep); the watermark is ``agent_run.updated_at``
(there is no ``completed_at`` column). Only ``status = 'success'`` runs are
fed: a failed / timed-out run has no reply to score, and the rubric
(RT-ADR-23) needs a user<->assistant exchange.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import AgentRunRow, ThreadMetaRow


@dataclass(frozen=True)
class QualityCandidate:
    """One finished run eligible for quality sampling."""

    run_id: UUID
    tenant_id: UUID
    thread_id: UUID
    agent_name: str
    agent_version: str
    updated_at: datetime


class QualityCandidateSource(abc.ABC):
    """Feed of successfully-finished runs past a watermark."""

    @abc.abstractmethod
    async def list_candidates(self, *, since: datetime, limit: int) -> list[QualityCandidate]:
        """Successful runs with ``updated_at > since``, oldest first.

        Ordered by ``updated_at`` so the caller can advance its watermark to
        the last item. Cross-tenant — run under the RLS-bypass scope.
        """


class SqlQualityCandidateSource(QualityCandidateSource):
    """Postgres feed: join ``agent_run`` x ``thread_meta`` for agent identity."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_candidates(self, *, since: datetime, limit: int) -> list[QualityCandidate]:
        stmt = (
            select(
                AgentRunRow.id,
                AgentRunRow.tenant_id,
                AgentRunRow.thread_id,
                ThreadMetaRow.agent_name,
                ThreadMetaRow.agent_version,
                AgentRunRow.updated_at,
            )
            .join(ThreadMetaRow, ThreadMetaRow.thread_id == AgentRunRow.thread_id)
            .where(
                AgentRunRow.status == "success",
                AgentRunRow.updated_at > since,
                ThreadMetaRow.agent_name.is_not(None),
            )
            .order_by(AgentRunRow.updated_at.asc())
            .limit(limit)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()
        return [
            QualityCandidate(
                run_id=row.id,
                tenant_id=row.tenant_id,
                thread_id=row.thread_id,
                agent_name=row.agent_name,
                agent_version=row.agent_version or "-",
                updated_at=row.updated_at,
            )
            for row in rows
        ]


class InMemoryQualityCandidateSource(QualityCandidateSource):
    """Test feed — a fixed candidate list filtered by the watermark."""

    def __init__(self, candidates: list[QualityCandidate] | None = None) -> None:
        self._candidates = list(candidates or [])

    async def list_candidates(self, *, since: datetime, limit: int) -> list[QualityCandidate]:
        fresh = sorted(
            (c for c in self._candidates if c.updated_at > since),
            key=lambda c: c.updated_at,
        )
        return fresh[:limit]


__all__ = [
    "InMemoryQualityCandidateSource",
    "QualityCandidate",
    "QualityCandidateSource",
    "SqlQualityCandidateSource",
]
