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
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from expert_work.persistence.approval import ApprovalStore
from expert_work.persistence.artifact import ArtifactStore
from expert_work.persistence.image_upload import ImageUploadStore
from expert_work.protocol import ApprovalStatus
from expert_work.runtime.storage import ObjectStore

logger = logging.getLogger(__name__)

# The cleanup runs against a DB connection that's already authenticated
# as a role with DELETE privilege on the target tables — typically
# ``retention_cleanup_worker`` (NOLOGIN role from migration 0010;
# operators ``ALTER ROLE ... WITH LOGIN`` for the cron user, or assign
# the role to a separate LOGIN account that's a member). We deliberately
# do NOT issue ``SET LOCAL ROLE`` in this code path: under asyncpg +
# SQLAlchemy 2.0, a ``SET LOCAL ROLE`` followed by a DELETE that
# actually matches rows intermittently returns "permission denied"
# even when ``has_table_privilege`` confirms the GRANT. Connecting
# directly as the worker role sidesteps the issue entirely.


@dataclass(frozen=True)
class CleanupReport:
    """Tally produced by one ``run_once`` sweep."""

    audit_deleted: int = 0
    audit_skipped_unacked: int = 0
    event_deleted: int = 0
    jwt_blacklist_deleted: int = 0
    # Mini-ADR J-32 (J.6.补强-3b) — image lifecycle hard-delete counts.
    image_uploads_hard_deleted: int = 0
    image_object_keys_removed: int = 0
    image_object_keys_failed: int = 0
    # Mini-ADR J-25 (J.9-step1) — artifact lifecycle counts.
    artifacts_soft_deleted: int = 0
    artifacts_hard_deleted: int = 0
    # Mini-ADR J-24 (J.8-step3b) — approvals auto-rejected past their
    # 24h ``timeout_at``.
    approvals_timed_out: int = 0
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
        image_upload_store: ImageUploadStore | None = None,
        object_store: ObjectStore | None = None,
        image_retention_days: int = 90,
        artifact_store: ArtifactStore | None = None,
        artifact_retention_days: int = 90,
        artifact_hard_delete_grace_days: int = 60,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        if batch_size <= 0:
            msg = "batch_size must be positive"
            raise ValueError(msg)
        if image_retention_days < 1:
            msg = "image_retention_days must be >= 1"
            raise ValueError(msg)
        if artifact_retention_days < 1:
            msg = "artifact_retention_days must be >= 1"
            raise ValueError(msg)
        if artifact_hard_delete_grace_days < 1:
            msg = "artifact_hard_delete_grace_days must be >= 1"
            raise ValueError(msg)
        self._sf = db_session_factory
        self._batch_size = batch_size
        self._image_upload_store = image_upload_store
        self._object_store = object_store
        self._image_retention_days = image_retention_days
        self._artifact_store = artifact_store
        self._artifact_retention_days = artifact_retention_days
        self._artifact_hard_delete_grace_days = artifact_hard_delete_grace_days
        self._approval_store = approval_store

    async def run_once(self) -> CleanupReport:
        """Run the retention passes once and return a tally.

        Each pass owns its own session + transaction so the
        ``SET LOCAL ROLE`` is re-issued cleanly per pass. Sharing one
        session across all three passes triggered intermittent
        ``permission denied`` failures on later DELETEs in CI even
        though the role had the grants — the per-pass isolation
        avoids whatever cross-statement state interaction caused that.

        Mini-ADR J-32 (J.6.补强-3b) — the image-upload pass runs when
        both an :class:`ImageUploadStore` and an :class:`ObjectStore`
        are wired; otherwise it's a no-op (the audit / event / jwt
        passes still run, so unit tests that don't care about images
        keep working).
        """
        started = time.monotonic()
        audit_deleted, audit_by_tenant = await self._delete_audit_log()
        audit_skipped = await self._count_unacked_past_retention()
        event_deleted = await self._delete_event_log()
        jwt_deleted = await self._delete_expired_jwt_blacklist()
        image_rows, image_keys_ok, image_keys_failed = await self._delete_expired_images()
        artifact_soft, artifact_hard = await self._sweep_artifacts()
        approvals_timed_out = await self._sweep_approval_timeouts()

        return CleanupReport(
            audit_deleted=audit_deleted,
            audit_skipped_unacked=audit_skipped,
            audit_deleted_by_tenant=audit_by_tenant,
            event_deleted=event_deleted,
            jwt_blacklist_deleted=jwt_deleted,
            image_uploads_hard_deleted=image_rows,
            image_object_keys_removed=image_keys_ok,
            image_object_keys_failed=image_keys_failed,
            artifacts_soft_deleted=artifact_soft,
            artifacts_hard_deleted=artifact_hard,
            approvals_timed_out=approvals_timed_out,
            duration_seconds=time.monotonic() - started,
        )

    async def _sweep_approval_timeouts(self) -> int:
        """Mini-ADR J-24 (J.8-step3b) — auto-reject approvals past 24h.

        A run paused for human approval has a ``timeout_at`` (default
        ``requested_at + 24h``). A pending row past that horizon is
        auto-rejected: ``mark_decided`` flips it to ``TIMEOUT`` with
        ``decided_by='system'``, so a later ``POST .../resume`` is
        refused (409 already-decided) and the paused checkpoint becomes
        logically dead — no run pins an approval slot forever.

        No-op when no :class:`ApprovalStore` is wired (unit-test path /
        deployments not running J.8).
        """
        if self._approval_store is None:
            return 0
        now = datetime.now(UTC)
        expired = await self._approval_store.list_expired(before=now, limit=self._batch_size)
        timed_out = 0
        for row in expired:
            ok = await self._approval_store.mark_decided(
                run_id=row.run_id,
                tenant_id=row.tenant_id,
                status=ApprovalStatus.TIMEOUT,
                decided_by="system",
                decided_at=now,
            )
            if ok:
                timed_out += 1
        return timed_out

    async def _sweep_artifacts(self) -> tuple[int, int]:
        """Mini-ADR J-25 (J.9-step1) — two-stage artifact lifecycle sweep.

        Stage 1: active rows past ``artifact_retention_days`` → soft-delete
        (sets ``deleted_at``). Stage 2: soft-deleted rows past
        ``artifact_hard_delete_grace_days`` → hard-delete (row +
        version rows).

        No-op when :class:`ArtifactStore` is not wired (unit-test path
        + deployments not running J.9). Workspace files are *not*
        removed here — J.15 volume lifecycle (Mini-ADR J-36) owns the
        underlying bytes; ``J.9-step1`` deliberately stops at the
        metadata. The follow-up archive 中间档 (tar.zst → ObjectStore)
        will land in a later step that reuses the J.15 archive flow.
        """
        if self._artifact_store is None:
            return 0, 0
        now = datetime.now(UTC)
        # Stage 1 — soft-delete active rows past retention.
        soft_cutoff = now - timedelta(days=self._artifact_retention_days)
        active = await self._artifact_store.list_active_past_retention(
            before=soft_cutoff, limit=self._batch_size
        )
        soft_count = 0
        for row in active:
            ok = await self._artifact_store.soft_delete(
                tenant_id=row.tenant_id,
                user_id=row.user_id,
                name=row.name,
                now=now,
            )
            if ok:
                soft_count += 1
        # Stage 2 — hard-delete soft-deleted rows past the grace window.
        hard_cutoff = now - timedelta(days=self._artifact_hard_delete_grace_days)
        expired = await self._artifact_store.list_expired(
            before=hard_cutoff, limit=self._batch_size
        )
        if not expired:
            return soft_count, 0
        hard_count = await self._artifact_store.hard_delete(artifact_ids=[a.id for a in expired])
        return soft_count, hard_count

    async def _delete_expired_images(self) -> tuple[int, int, int]:
        """Remove image rows past their retention window + their bytes.

        Mini-ADR J-32 — finds ``image_upload`` rows whose ``created_at``
        is older than ``now - image_retention_days`` (regardless of
        ``deleted_at`` state — both never-deleted-but-old and
        already-soft-deleted rows leave at the same horizon), removes
        the object-store key for each, and hard-deletes the row.

        Object-store failures don't block row hard-delete: an orphaned
        key is a far smaller correctness problem than a stuck row
        whose key never goes away (the bytes are billed against
        ``IMAGE_STORAGE_BYTES``). The failure count lands in the report
        so SRE can investigate without the sweep stalling.

        Returns ``(rows_deleted, keys_removed, keys_failed)``. Returns
        ``(0, 0, 0)`` when either store is missing (unit-test path).
        """
        if self._image_upload_store is None or self._object_store is None:
            return 0, 0, 0
        cutoff = datetime.now(UTC) - timedelta(days=self._image_retention_days)
        expired = await self._image_upload_store.list_expired(
            before=cutoff,
            limit=self._batch_size,
        )
        if not expired:
            return 0, 0, 0
        keys_ok = 0
        keys_failed = 0
        for row in expired:
            try:
                await self._object_store.delete(row.object_key)
                keys_ok += 1
            except Exception:
                keys_failed += 1
                logger.exception(
                    "retention.image_object_delete_failed key=%s",
                    row.object_key,
                )
        rows = await self._image_upload_store.hard_delete(
            image_ids=[r.id for r in expired],
        )
        return rows, keys_ok, keys_failed

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
        """Two-step: read retentions, then per-tenant flat DELETE.

        Investigation in CI showed that ``DELETE FROM event_log WHERE
        ctid IN (SELECT … LIMIT N)`` consistently raises ``permission
        denied for table event_log`` even though ``has_table_privilege``
        + a trivial probe ``DELETE FROM event_log WHERE id = -999999``
        both succeed for the same role in the same session. The
        ``ctid``-subquery + ``LIMIT`` form is the only thing that
        differs from the audit_log path that *does* work — and rather
        than chase the asyncpg/SQLAlchemy quirk further, the flat
        ``DELETE … WHERE tenant_id = :t AND created_at < :cutoff``
        form is plenty for M0 retention volumes. M1 can add ``LIMIT``
        back if the table grows large enough to need batching, by
        which time we'll have partitioning anyway.
        """
        retentions = await self._read_event_retentions()
        total = 0
        for tenant_id, days in retentions:
            async with self._sf() as session:
                result = await session.execute(
                    text(
                        "DELETE FROM event_log "
                        "WHERE tenant_id = :t "
                        "  AND created_at < now() - make_interval(days => :d) "
                        "RETURNING id"
                    ),
                    {"t": tenant_id, "d": days},
                )
                total += len(result.fetchall())
                await session.commit()
        return total

    async def _read_event_retentions(self) -> list[tuple[str, int]]:
        """Return ``(tenant_id, event_log_retention_days)`` for every tenant."""
        async with self._sf() as session:
            result = await session.execute(
                text("SELECT tenant_id::text, event_log_retention_days FROM tenant_config")
            )
            rows = [(str(r[0]), int(r[1])) for r in result.fetchall()]
            await session.commit()
        return rows

    async def _delete_expired_jwt_blacklist(self) -> int:
        """``jwt_blacklist`` is global — no tenant_id, expire_at-driven."""
        async with self._sf() as session:
            result = await session.execute(
                text("DELETE FROM jwt_blacklist WHERE expires_at < now() RETURNING jti")
            )
            rows = result.fetchall()
            await session.commit()
        return len(rows)
