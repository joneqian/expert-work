"""``RetentionCleanupJob`` — the D.3 nightly sweep.

Per STREAM-D-DESIGN § 2.6 + Mini-ADR D-5: M0 walks the rows with
``DELETE ... WHERE ctid IN (SELECT ... LIMIT N)`` rather than partition
drops. Simple, no schema churn, and the per-tenant retention shapes
fit in a single SQL statement using a JOIN with ``tenant_config``.

Three independent passes per ``run_once``:

1.  ``audit_log`` — only ``backup_acked = true`` rows past
    ``audit_retention_days``. Unacked candidates are counted + logged
    so SRE notices when the D.1c worker is lagging; the rows
    themselves are **never** deleted while unacked.
2.  ``event_log`` — past ``event_log_retention_days``. No WORM gate
    in M0 (cold archive to S3 is a Stream G item).
3.  ``jwt_blacklist`` — past ``expires_at``. Global, not tenant-scoped.

The whole sweep runs as ``retention_cleanup_worker`` (migration 0010,
NOLOGIN BYPASSRLS with the minimum delete grants).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# The cleanup runs as the dedicated worker role; this is the only
# place outside the audit-backup worker that holds DELETE on these
# tables.
_RETENTION_WORKER_ROLE: Final[str] = "retention_cleanup_worker"
_SET_RETENTION_WORKER_ROLE = text(f"SET LOCAL ROLE {_RETENTION_WORKER_ROLE}")


@dataclass(frozen=True)
class CleanupReport:
    """Tally produced by one ``run_once`` sweep."""

    audit_deleted: int = 0
    audit_skipped_unacked: int = 0
    event_deleted: int = 0
    jwt_blacklist_deleted: int = 0
    duration_seconds: float = 0.0
    # Per-tenant breakdown of audit deletes (for observability).
    audit_deleted_by_tenant: dict[str, int] = field(default_factory=dict)


class RetentionCleanupJob:
    """One-shot retention sweep driven by ``tenant_config`` per-tenant TTLs."""

    def __init__(
        self,
        *,
        db_session_factory: async_sessionmaker[AsyncSession],
        batch_size: int = 10000,
    ) -> None:
        if batch_size <= 0:
            msg = "batch_size must be positive"
            raise ValueError(msg)
        self._sf = db_session_factory
        self._batch_size = batch_size

    async def run_once(self) -> CleanupReport:
        """Run the three retention passes once and return a tally.

        Each pass owns its own session + transaction so the
        ``SET LOCAL ROLE`` is re-issued cleanly per pass. Sharing one
        session across all three passes triggered intermittent
        ``permission denied`` failures on later DELETEs in CI even
        though the role had the grants — the per-pass isolation
        avoids whatever cross-statement state interaction caused that.
        """
        started = time.monotonic()
        audit_deleted, audit_by_tenant = await self._delete_audit_log()
        audit_skipped = await self._count_unacked_past_retention()
        event_deleted = await self._delete_event_log()
        jwt_deleted = await self._delete_expired_jwt_blacklist()

        return CleanupReport(
            audit_deleted=audit_deleted,
            audit_skipped_unacked=audit_skipped,
            audit_deleted_by_tenant=audit_by_tenant,
            event_deleted=event_deleted,
            jwt_blacklist_deleted=jwt_deleted,
            duration_seconds=time.monotonic() - started,
        )

    # ------------------------------------------------------------------
    # Per-table helpers (private). Each opens its own session + txn,
    # SETs LOCAL ROLE retention_cleanup_worker, runs one statement,
    # commits.
    # ------------------------------------------------------------------

    async def _delete_audit_log(self) -> tuple[int, dict[str, int]]:
        """Delete acked audit rows past their tenant's retention window.

        Uses ``ctid`` subquery to apply LIMIT to a DELETE (Postgres
        doesn't support ``DELETE ... LIMIT`` directly). RETURNING
        ``tenant_id`` lets us tally per-tenant deletes for the report.

        The ``backup_acked = true`` predicate is the WORM safety
        gate: unacked rows are skipped here and counted separately
        by ``_count_unacked_past_retention``.
        """
        async with self._sf() as session:
            await session.execute(_SET_RETENTION_WORKER_ROLE)
            result = await session.execute(
                text(
                    """
                    DELETE FROM audit_log
                    WHERE ctid IN (
                        SELECT a.ctid
                        FROM audit_log a
                        JOIN tenant_config c ON c.tenant_id = a.tenant_id
                        WHERE a.backup_acked = true
                          AND a.occurred_at < now() - (c.audit_retention_days || ' days')::interval
                        LIMIT :batch
                    )
                    RETURNING tenant_id
                    """
                ),
                {"batch": self._batch_size},
            )
            rows = result.fetchall()
            await session.commit()
        per_tenant: dict[str, int] = {}
        for row in rows:
            tid = str(row[0])
            per_tenant[tid] = per_tenant.get(tid, 0) + 1
        return len(rows), per_tenant

    async def _count_unacked_past_retention(self) -> int:
        """How many audit rows are *past* retention but still unacked.

        Steady-state value is 0. A growing number means the D.1c
        WORM backup worker is falling behind and needs investigation;
        we surface it on the report but never delete those rows.
        """
        async with self._sf() as session:
            await session.execute(_SET_RETENTION_WORKER_ROLE)
            result = await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM audit_log a
                    JOIN tenant_config c ON c.tenant_id = a.tenant_id
                    WHERE a.backup_acked = false
                      AND a.occurred_at < now() - (c.audit_retention_days || ' days')::interval
                    """
                )
            )
            count = int(result.scalar() or 0)
            await session.commit()
        return count

    async def _delete_event_log(self) -> int:
        async with self._sf() as session:
            await session.execute(_SET_RETENTION_WORKER_ROLE)
            # DIAGNOSTIC (D.3): the CI integration test hit a
            # "permission denied for table event_log" we can't repro
            # locally. ``print`` (not logger) so pytest's stdout
            # capture shows the values on failure.
            diag_result = await session.execute(
                text(
                    "SELECT current_user, session_user, "
                    "has_table_privilege(current_user, 'event_log', 'DELETE') as can_del, "
                    "has_table_privilege(current_user, 'event_log', 'SELECT') as can_sel, "
                    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) as bypass"
                )
            )
            diag = diag_result.first()
            if diag is not None:
                print(
                    f"[D.3 DIAG] event_log user={diag[0]} session={diag[1]} "
                    f"can_delete={diag[2]} can_select={diag[3]} bypassrls={diag[4]}"
                )
            result = await session.execute(
                text(
                    """
                    DELETE FROM event_log
                    WHERE ctid IN (
                        SELECT e.ctid
                        FROM event_log e
                        JOIN tenant_config c ON c.tenant_id = e.tenant_id
                        WHERE e.created_at <
                              now() - (c.event_log_retention_days || ' days')::interval
                        LIMIT :batch
                    )
                    """
                ),
                {"batch": self._batch_size},
            )
            rowcount = int(result.rowcount or 0)  # type: ignore[attr-defined]
            await session.commit()
        return rowcount

    async def _delete_expired_jwt_blacklist(self) -> int:
        """``jwt_blacklist`` is global — no tenant_id, expire_at-driven."""
        async with self._sf() as session:
            await session.execute(_SET_RETENTION_WORKER_ROLE)
            result = await session.execute(
                text(
                    """
                    DELETE FROM jwt_blacklist
                    WHERE ctid IN (
                        SELECT ctid FROM jwt_blacklist
                        WHERE expires_at < now()
                        LIMIT :batch
                    )
                    """
                ),
                {"batch": self._batch_size},
            )
            rowcount = int(result.rowcount or 0)  # type: ignore[attr-defined]
            await session.commit()
        return rowcount
