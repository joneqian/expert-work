"""Tenant billing-ledger store — Stream Y (Mini-ADR Y-4).

CRUD-light store over ``tenant_billing_ledger`` — the derived per-tenant
monthly billing buckets. The Y4 rollup job is the only writer; it ``upsert``s a
bucket per ``(tenant_id, month, provider, model, agent_name)``.

The table is **tenant-scoped** (per-tenant rows, standard tenant RLS), unlike
the platform ``model_rate_card``. SQL callers therefore drive these methods
under the tenant's RLS scope (``current_tenant_id_var`` set), exactly like the
``token_usage`` store — the rollup writes ledger rows under each tenant's scope
so the RLS ``WITH CHECK`` passes.

Idempotency strategy — **upsert-overwrite**: ``upsert`` uses
``on_conflict_do_update`` on the bucket unique key, replacing all sums + cost
fields. Re-running the rollup for a month recomputes each bucket and overwrites
it in place, so a re-run is exact (never additive). ``delete_month`` exists for
callers that want delete-then-insert semantics (e.g. to evict a bucket that has
disappeared from usage), but the rollup relies on upsert-overwrite: usage rows
are append-only, so a bucket never *shrinks out of existence* between runs.
"""

from __future__ import annotations

import abc
import asyncio
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.persistence.models import TenantBillingLedgerRow
from helix_agent.protocol import TenantBillingLedgerRecord


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


#: Unique key identifying a billing bucket (declared in migration 0060).
_BUCKET_CONSTRAINT = "tenant_billing_ledger_bucket_uniq"


class TenantBillingLedgerStore(abc.ABC):
    """Upsert + read for derived per-tenant monthly billing buckets."""

    @abc.abstractmethod
    async def upsert(self, record: TenantBillingLedgerRecord) -> TenantBillingLedgerRecord:
        """Insert-or-overwrite the bucket keyed by
        ``(tenant_id, month, provider, model, agent_name)``.

        On conflict every sum + cost field (plus ``priced`` /
        ``rate_card_priced_at`` / ``updated_at``) is replaced — this is the
        idempotency anchor, so a rollup re-run recomputes rather than
        double-counts.
        """

    @abc.abstractmethod
    async def list_for_tenant(
        self, *, tenant_id: UUID, month: date
    ) -> list[TenantBillingLedgerRecord]:
        """Return all buckets for ``tenant_id`` in ``month``."""

    @abc.abstractmethod
    async def delete_month(self, *, tenant_id: UUID, month: date) -> int:
        """Delete every bucket for ``tenant_id`` in ``month``; return the count.

        Not used by the rollup (it relies on upsert-overwrite). Provided for
        callers wanting strict delete-then-insert semantics.
        """


class InMemoryTenantBillingLedgerStore(TenantBillingLedgerStore):
    """Dict-backed ledger store keyed by the bucket tuple; lock-guarded."""

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, date, str, str, str], TenantBillingLedgerRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(
        record: TenantBillingLedgerRecord,
    ) -> tuple[UUID, date, str, str, str]:
        return (
            record.tenant_id,
            record.month,
            record.provider,
            record.model,
            record.agent_name,
        )

    async def upsert(self, record: TenantBillingLedgerRecord) -> TenantBillingLedgerRecord:
        key = self._key(record)
        now = _utc_now()
        async with self._lock:
            existing = self._rows.get(key)
            if existing is None:
                stored = record.model_copy(update={"created_at": now, "updated_at": now})
            else:
                # Overwrite the prior bucket's mutable fields; keep its id +
                # created_at stable (the row's identity).
                stored = record.model_copy(
                    update={
                        "id": existing.id,
                        "created_at": existing.created_at,
                        "updated_at": now,
                    }
                )
            self._rows[key] = stored
            return stored

    async def list_for_tenant(
        self, *, tenant_id: UUID, month: date
    ) -> list[TenantBillingLedgerRecord]:
        async with self._lock:
            rows = [r for r in self._rows.values() if r.tenant_id == tenant_id and r.month == month]
        rows.sort(key=lambda r: (r.provider, r.model, r.agent_name))
        return rows

    async def delete_month(self, *, tenant_id: UUID, month: date) -> int:
        async with self._lock:
            victims = [
                k for k, r in self._rows.items() if r.tenant_id == tenant_id and r.month == month
            ]
            for k in victims:
                del self._rows[k]
        return len(victims)


def _row_to_record(row: TenantBillingLedgerRow) -> TenantBillingLedgerRecord:
    return TenantBillingLedgerRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        month=row.month,
        provider=row.provider,
        model=row.model,
        agent_name=row.agent_name,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_creation_tokens=row.cache_creation_tokens,
        cache_read_tokens=row.cache_read_tokens,
        base_cost_micros=row.base_cost_micros,
        markup_cost_micros=row.markup_cost_micros,
        billed_cost_micros=row.billed_cost_micros,
        priced=row.priced,
        rate_card_priced_at=row.rate_card_priced_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class DbTenantBillingLedgerStore(TenantBillingLedgerStore):
    """Postgres-backed ledger store (RLS-scoped sessions).

    ``session_factory`` must be the RLS-wrapped sessionmaker: the tenant GUC is
    set after BEGIN, so the ``tenant_billing_ledger`` RLS policy scopes every
    row to the calling tenant. Writes whose tenant context is unset fail the
    policy ``WITH CHECK`` — RLS, not trust.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def upsert(self, record: TenantBillingLedgerRecord) -> TenantBillingLedgerRecord:
        now = _utc_now()
        stmt = (
            pg_insert(TenantBillingLedgerRow)
            .values(
                tenant_id=record.tenant_id,
                month=record.month,
                provider=record.provider,
                model=record.model,
                agent_name=record.agent_name,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cache_creation_tokens=record.cache_creation_tokens,
                cache_read_tokens=record.cache_read_tokens,
                base_cost_micros=record.base_cost_micros,
                markup_cost_micros=record.markup_cost_micros,
                billed_cost_micros=record.billed_cost_micros,
                priced=record.priced,
                rate_card_priced_at=record.rate_card_priced_at,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint=_BUCKET_CONSTRAINT,
                set_={
                    "input_tokens": record.input_tokens,
                    "output_tokens": record.output_tokens,
                    "cache_creation_tokens": record.cache_creation_tokens,
                    "cache_read_tokens": record.cache_read_tokens,
                    "base_cost_micros": record.base_cost_micros,
                    "markup_cost_micros": record.markup_cost_micros,
                    "billed_cost_micros": record.billed_cost_micros,
                    "priced": record.priced,
                    "rate_card_priced_at": record.rate_card_priced_at,
                    "updated_at": now,
                },
            )
            .returning(TenantBillingLedgerRow)
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one()
            await session.commit()
        return _row_to_record(row)

    async def list_for_tenant(
        self, *, tenant_id: UUID, month: date
    ) -> list[TenantBillingLedgerRecord]:
        stmt = (
            select(TenantBillingLedgerRow)
            .where(
                TenantBillingLedgerRow.tenant_id == tenant_id,
                TenantBillingLedgerRow.month == month,
            )
            .order_by(
                TenantBillingLedgerRow.provider,
                TenantBillingLedgerRow.model,
                TenantBillingLedgerRow.agent_name,
            )
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def delete_month(self, *, tenant_id: UUID, month: date) -> int:
        stmt = (
            sa_delete(TenantBillingLedgerRow)
            .where(
                TenantBillingLedgerRow.tenant_id == tenant_id,
                TenantBillingLedgerRow.month == month,
            )
            .returning(TenantBillingLedgerRow.id)
        )
        async with self._sf() as session:
            deleted = (await session.execute(stmt)).scalars().all()
            await session.commit()
        return len(deleted)
