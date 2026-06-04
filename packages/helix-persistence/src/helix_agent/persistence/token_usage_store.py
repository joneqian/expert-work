"""Token-usage store — Stream G.9.

Persists one row per LLM call so dashboards + budget queries can roll
up tokens per ``(tenant, agent_name, agent_version, model)`` over any
time window. Two implementations behind one ABC, matching the audit-
log / feedback store pattern:

* :class:`InMemoryTokenUsageStore` — dev / unit tests (no durability).
* :class:`DbTokenUsageStore` — Postgres-backed; RLS-scoped via the
  sessionmaker wrapper (``app.tenant_id`` GUC set after BEGIN, so the
  ``token_usage`` RLS policy applies).

The C.5 ``token_budget_ledger`` table (one row per ``(tenant, month)``
rollup) remains independent — G.9 is for analytics + per-agent
visibility, the ledger is for budget enforcement's fast path. M1-D
will revisit whether to fold the ledger into roll-ups over this table.
"""

from __future__ import annotations

import abc
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models.token_usage import TokenUsageRow


@dataclass(frozen=True)
class TokenUsageRecord:
    """One LLM-call row. ``id`` / ``observed_at`` are ``None`` pre-insert."""

    tenant_id: UUID
    agent_name: str
    agent_version: str
    model: str
    # Stream Y-3 — additive nullable provider so Y4 can price by
    # ``(provider, model)``. ``None`` for legacy rows.
    provider: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    trace_id: str | None = None
    id: int | None = None
    observed_at: datetime | None = None


class TokenUsageStore(abc.ABC):
    """Append + read-by-tenant for per-call token usage."""

    @abc.abstractmethod
    async def insert(self, record: TokenUsageRecord) -> TokenUsageRecord:
        """Persist one row; return it with ``id`` + ``observed_at`` filled."""

    @abc.abstractmethod
    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        agent_name: str | None = None,
        model: str | None = None,
        limit: int = 100,
    ) -> Sequence[TokenUsageRecord]:
        """Return rows for ``tenant_id``, newest first.

        Tenant scoping rides on the RLS context (the GUC / contextvar),
        not a SQL filter — so this also acts as the cross-tenant
        isolation check at the data layer.
        """

    @abc.abstractmethod
    async def list_for_tenant_window(
        self,
        *,
        tenant_id: UUID,
        start: datetime,
        end: datetime,
    ) -> Sequence[TokenUsageRecord]:
        """Return **all** rows with ``start <= observed_at < end`` — Stream Y4.

        Half-open window (start inclusive, end exclusive). No row cap: the Y4
        rollup must price every usage row in a month, so this reads the full
        window (the SQL impl pages internally). Tenant scoping rides on the RLS
        context, like :meth:`list_for_tenant`.
        """


class InMemoryTokenUsageStore(TokenUsageStore):
    """In-memory :class:`TokenUsageStore` — dev / unit tests."""

    def __init__(self) -> None:
        self._rows: list[TokenUsageRecord] = []
        self._ids = itertools.count(1)

    async def insert(self, record: TokenUsageRecord) -> TokenUsageRecord:
        stored = replace(record, id=next(self._ids), observed_at=datetime.now(UTC))
        self._rows.append(stored)
        return stored

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        agent_name: str | None = None,
        model: str | None = None,
        limit: int = 100,
    ) -> Sequence[TokenUsageRecord]:
        rows = [r for r in self._rows if r.tenant_id == tenant_id]
        if agent_name is not None:
            rows = [r for r in rows if r.agent_name == agent_name]
        if model is not None:
            rows = [r for r in rows if r.model == model]
        rows.sort(key=lambda r: r.id or 0, reverse=True)
        return rows[:limit]

    async def list_for_tenant_window(
        self,
        *,
        tenant_id: UUID,
        start: datetime,
        end: datetime,
    ) -> Sequence[TokenUsageRecord]:
        return [
            r
            for r in self._rows
            if r.tenant_id == tenant_id
            and r.observed_at is not None
            and start <= r.observed_at < end
        ]


class DbTokenUsageStore(TokenUsageStore):
    """Postgres-backed :class:`TokenUsageStore`.

    ``session_factory`` must be the RLS-wrapped sessionmaker
    (:func:`helix_agent.persistence.rls.build_rls_sessionmaker`): the
    tenant GUC is set after BEGIN, so the ``token_usage`` RLS policy
    scopes every row to the calling tenant. An ``insert`` whose tenant
    context is unset fails the policy ``WITH CHECK`` — RLS, not trust.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def insert(self, record: TokenUsageRecord) -> TokenUsageRecord:
        async with self._sf() as session:
            row = TokenUsageRow(
                tenant_id=record.tenant_id,
                agent_name=record.agent_name,
                agent_version=record.agent_version,
                model=record.model,
                provider=record.provider,
                trace_id=record.trace_id,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cache_creation_tokens=record.cache_creation_tokens,
                cache_read_tokens=record.cache_read_tokens,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            stored = _row_to_record(row)
            await session.commit()
            return stored

    async def list_for_tenant(
        self,
        *,
        tenant_id: UUID,
        agent_name: str | None = None,
        model: str | None = None,
        limit: int = 100,
    ) -> Sequence[TokenUsageRecord]:
        async with self._sf() as session:
            stmt = select(TokenUsageRow)
            if agent_name is not None:
                stmt = stmt.where(TokenUsageRow.agent_name == agent_name)
            if model is not None:
                stmt = stmt.where(TokenUsageRow.model == model)
            stmt = stmt.order_by(TokenUsageRow.id.desc()).limit(limit)
            result = await session.execute(stmt)
            return [_row_to_record(r) for r in result.scalars().all()]

    async def list_for_tenant_window(
        self,
        *,
        tenant_id: UUID,
        start: datetime,
        end: datetime,
    ) -> Sequence[TokenUsageRecord]:
        # Keyset pagination by ascending id: no row cap, bounded memory per
        # page. ``observed_at`` is monotonic with insert but not guaranteed
        # unique, so we page on the surrogate ``id`` instead.
        #
        # NOTE (Y4 operability follow-up): all pages stream inside ONE session,
        # so a very large tenant-month holds a read transaction (+ connection)
        # open for the whole scan. Acceptable for the M1 rollup cadence and a
        # direct-to-Postgres connection (see the rollup job's settings); revisit
        # with per-page sessions if the job runs behind transaction-mode pooling
        # or tenants accumulate millions of rows per month.
        page_size = 5000
        out: list[TokenUsageRecord] = []
        after_id = 0
        async with self._sf() as session:
            while True:
                stmt = (
                    select(TokenUsageRow)
                    .where(
                        TokenUsageRow.observed_at >= start,
                        TokenUsageRow.observed_at < end,
                        TokenUsageRow.id > after_id,
                    )
                    .order_by(TokenUsageRow.id.asc())
                    .limit(page_size)
                )
                rows = (await session.execute(stmt)).scalars().all()
                if not rows:
                    break
                out.extend(_row_to_record(r) for r in rows)
                after_id = rows[-1].id
                if len(rows) < page_size:
                    break
        return out


def _row_to_record(row: TokenUsageRow) -> TokenUsageRecord:
    return TokenUsageRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        agent_name=row.agent_name,
        agent_version=row.agent_version,
        model=row.model,
        provider=row.provider,
        trace_id=row.trace_id,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_creation_tokens=row.cache_creation_tokens,
        cache_read_tokens=row.cache_read_tokens,
        observed_at=row.observed_at,
    )
