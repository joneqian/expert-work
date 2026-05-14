"""``AuditWormBackupWorker`` — the D.1c sweep loop.

Reads unacked ``audit_log`` rows in id order, writes them to an
Object-Lock-enabled bucket with compliance retention, then flips
``backup_acked`` true. See STREAM-D-DESIGN § 2.4 + Mini-ADR D-3.

Concurrency contract:

* One worker instance per deployment. Multiple workers racing on the
  same backlog would create duplicate object versions (harmless under
  compliance lock — each version is independently retained — but
  wasteful). M1 may add a per-tenant work-stealing partition; M0
  keeps it single.
* Reads + the matching UPDATE happen in **separate transactions** so
  the read txn doesn't hold a lock during the slow S3 round trip.
  Worst case if the worker crashes between put and UPDATE: the row
  stays unacked, next sweep puts again (new version, same retention)
  and finishes the UPDATE.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from audit_backup_worker.serialization import object_key_for, serialize_row
from helix_agent.persistence.models import AuditLogRow
from helix_agent.runtime.storage import ObjectStore

logger = logging.getLogger(__name__)

# The worker's reads + targeted UPDATEs run as ``audit_backup_worker``:
#  - BYPASSRLS so we can scan every tenant's rows.
#  - SELECT on audit_log + UPDATE only on (backup_acked, backup_acked_at)
#    (from migration 0009). UPDATE on any other column would 42501.
_AUDIT_BACKUP_WORKER_ROLE: Final[str] = "audit_backup_worker"
_SET_AUDIT_BACKUP_WORKER_ROLE = text(f"SET LOCAL ROLE {_AUDIT_BACKUP_WORKER_ROLE}")


# A pluggable resolver lets D.3 swap in a TenantConfigService lookup
# without touching the worker. M0 ships ``static_retention_resolver``
# returning a single default.
RetentionResolver = Callable[[UUID], Awaitable[int]]


def static_retention_resolver(days: int) -> RetentionResolver:
    """Return a resolver that always yields ``days``.

    Used until D.3 lands per-tenant ``audit_retention_days``.
    """

    async def _resolve(_tenant_id: UUID) -> int:
        return days

    return _resolve


@dataclass(frozen=True)
class AuditBackupResult:
    """Outcome of one ``run_one_batch`` sweep."""

    processed: int
    """Rows successfully written + acked in this sweep."""

    failed: int
    """Rows that errored (still unacked; will retry next sweep)."""


class AuditWormBackupWorker:
    """Stream D.1c sweep loop.

    Constructor parameters:

    :param db_session_factory: yields :class:`AsyncSession`. The
        underlying engine must connect as a role that's been ``GRANT
        audit_backup_worker``ed; the worker ``SET LOCAL ROLE``s
        inside its transactions.
    :param object_store: target of locked writes. Must point at a
        bucket with Object Lock enabled (configured via IaC).
    :param retention_resolver: ``await retention_resolver(tenant_id)``
        returns the retention period in days. M0 uses a global
        default; D.3 swaps in a per-tenant lookup.
    :param batch_size: rows per sweep. Bigger means fewer DB round
        trips; smaller means tighter per-row latency. 100 by default.
    """

    def __init__(
        self,
        *,
        db_session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        retention_resolver: RetentionResolver,
        batch_size: int = 100,
    ) -> None:
        if batch_size <= 0:
            msg = "batch_size must be positive"
            raise ValueError(msg)
        self._sf = db_session_factory
        self._object_store = object_store
        self._retention_resolver = retention_resolver
        self._batch_size = batch_size

    async def run_one_batch(self) -> AuditBackupResult:
        """Process up to ``batch_size`` unacked rows.

        Returns a tally; a fully drained queue is ``processed=0``.
        Per-row failures don't bubble — the row stays unacked and
        the next sweep retries.
        """
        rows = await self._read_unacked()
        if not rows:
            return AuditBackupResult(processed=0, failed=0)

        processed = 0
        failed = 0
        for row in rows:
            try:
                await self._backup_one(row)
                await self._mark_acked(row)
                processed += 1
            except Exception:
                logger.exception(
                    "audit.backup.row_failed id=%s tenant=%s",
                    row.id,
                    row.tenant_id,
                )
                failed += 1
        return AuditBackupResult(processed=processed, failed=failed)

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        poll_interval_s: float = 2.0,
    ) -> None:
        """Sweep in a loop until ``stop`` is set.

        On empty batches the loop sleeps ``poll_interval_s`` so it
        doesn't spin against the DB. On non-empty batches it loops
        immediately — the only reason to slow down is genuine
        backlog drain.
        """
        while not stop.is_set():
            result = await self.run_one_batch()
            if result.processed == 0 and result.failed == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
                except TimeoutError:
                    continue

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _read_unacked(self) -> list[AuditLogRow]:
        """Return up to ``batch_size`` unacked rows in id order.

        Backed by the partial index ``audit_log_backup_pending_idx``
        from migration 0008 — scan cost is O(unacked-count), not
        O(total-rows).
        """
        async with self._sf() as session:
            await session.execute(_SET_AUDIT_BACKUP_WORKER_ROLE)
            stmt = (
                select(AuditLogRow)
                .where(AuditLogRow.backup_acked.is_(False))
                .order_by(AuditLogRow.id)
                .limit(self._batch_size)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            # Detach so we can use the rows after the session closes
            # without lazy-load complaints.
            for row in rows:
                session.expunge(row)
        return rows

    async def _backup_one(self, row: AuditLogRow) -> None:
        """Serialize ``row`` + put with compliance lock."""
        days = await self._retention_resolver(row.tenant_id)
        retain_until = datetime.now(tz=UTC) + timedelta(days=days)
        await self._object_store.put(
            object_key_for(row),
            serialize_row(row),
            content_type="application/json",
            retain_until=retain_until,
            lock_mode="compliance",
        )

    async def _mark_acked(self, row: AuditLogRow) -> None:
        """Flip ``backup_acked`` true for one row.

        Column-level grant from migration 0009 lets the worker UPDATE
        only ``backup_acked`` + ``backup_acked_at`` — any other column
        would 42501.
        """
        async with self._sf() as session:
            await session.execute(_SET_AUDIT_BACKUP_WORKER_ROLE)
            await session.execute(
                update(AuditLogRow)
                .where(AuditLogRow.id == row.id)
                .values(backup_acked=True, backup_acked_at=datetime.now(tz=UTC))
            )
            await session.commit()
