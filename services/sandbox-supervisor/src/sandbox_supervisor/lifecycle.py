"""``VolumeLifecycleManager`` — Stream J.15-补强-2.

Owns two flows that share the same ``docker archive_volume`` mechanism:

* :meth:`archive_pending` — Mini-ADR J-36 lifecycle 第 2 → 第 3 档.
  Soft-deleted workspaces (``deleted_at IS NOT NULL AND
  archived_object_key IS NULL``) get tar.gz-streamed into ObjectStore,
  their row's ``archived_object_key`` filled, the docker volume removed.
  After this their disk footprint is the archive blob; physical hard-
  delete after the 90-day retention is M1 work.
* :meth:`backup_active` — Mini-ADR J-29 第 2 项 daily snapshot. Runs
  once a day (off-peak), tar.gz-streams every active workspace into a
  date-prefixed ObjectStore key. The retention-cleanup-job (推 M1 for
  the volume dimension; see runbook for the manual prune) keeps the
  rolling window inside ``workspace_backup_retention_days``.

Both flows route per-volume failures through :class:`VolumeBackupDLQ`
with the same K7-style backoff envelope; :meth:`drain_dlq` retries
ready rows and dead-letters after 5 attempts. The supervisor's reaper
loop calls :meth:`archive_pending` + :meth:`drain_dlq` on each tick;
:meth:`backup_active` is driven by a separate hour-of-day task in
``app.py``'s lifespan.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from helix_agent.persistence import (
    UserWorkspaceStore,
    VolumeBackupDLQ,
    VolumeDLQRow,
    WorkspaceNotFoundError,
)
from helix_agent.protocol import AuditEntry, UserWorkspace
from helix_agent.protocol.audit import AuditAction, AuditResult
from helix_agent.runtime.storage import ObjectStore, ObjectStoreError
from sandbox_supervisor.docker_client import DockerClient, DockerError
from sandbox_supervisor.settings import SandboxSupervisorSettings

logger = logging.getLogger(__name__)

#: K7-style backoff for DLQ retries: 1m / 5m / 30m / 2h / 6h. Indexed by
#: ``attempts`` after the failure that scheduled the retry — i.e.,
#: ``_BACKOFF_SECONDS[attempts - 1]`` for ``attempts >= 1``.
_BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 1800, 7200, 21600)

#: After this many retries the row is dead-letter — we push
#: ``next_retry_at`` 365 days into the future so it falls out of the
#: hot index. Operator picks up dead letters via runbook.
_MAX_ATTEMPTS = len(_BACKOFF_SECONDS)
_DEAD_LETTER_DELAY = timedelta(days=365)


class AuditSink(Protocol):
    """Structural protocol matching :class:`SandboxSupervisor.AuditSink`."""

    async def write(self, entry: AuditEntry) -> None:
        """Persist one audit entry."""


@dataclass(frozen=True)
class LifecycleResult:
    """Summary returned by :meth:`archive_pending`, :meth:`backup_active`,
    and :meth:`drain_dlq` — useful for metrics + tests."""

    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


def _archive_key(prefix: str, workspace: UserWorkspace) -> str:
    """ObjectStore key for a J-36 archive (lifecycle 第 3 档)."""
    return f"{prefix}/{workspace.tenant_id}/{workspace.user_id}/{workspace.volume_name}.tar.gz"


def _backup_key(prefix: str, workspace: UserWorkspace, date_str: str) -> str:
    """ObjectStore key for a J-29 第 2 项 daily backup snapshot."""
    return (
        f"{prefix}/{workspace.tenant_id}/{workspace.user_id}/"
        f"{date_str}/{workspace.volume_name}.tar.gz"
    )


def _next_retry_at(*, now: datetime, attempts_after_failure: int) -> datetime:
    """Compute :class:`VolumeBackupDLQ.record_failure` ``next_retry_at``.

    ``attempts_after_failure`` is the new ``attempts`` value (incremented
    by ``record_failure``). Returns ``now + 365 d`` for dead-letter once
    attempts crosses :data:`_MAX_ATTEMPTS`.
    """
    idx = attempts_after_failure - 1
    if idx >= _MAX_ATTEMPTS:
        return now + _DEAD_LETTER_DELAY
    return now + timedelta(seconds=_BACKOFF_SECONDS[idx])


@dataclass(frozen=True)
class VolumeLifecycleManager:
    """Drives volume archive + daily backup + DLQ retry.

    Constructed once per supervisor; the reaper calls
    :meth:`archive_pending` + :meth:`drain_dlq` on each tick, the daily
    task calls :meth:`backup_active`. All mutating calls swallow per-
    volume failures into the DLQ so a single bad volume doesn't stall
    the sweep.
    """

    workspace_store: UserWorkspaceStore
    dlq: VolumeBackupDLQ
    docker: DockerClient
    object_store: ObjectStore
    settings: SandboxSupervisorSettings
    audit: AuditSink
    service_name: str

    # ----- public sweep methods ---------------------------------------

    async def archive_pending(self) -> LifecycleResult:
        """Archive every soft-deleted workspace whose archive hasn't run yet.

        On success the workspace row's ``archived_object_key`` is filled
        and the docker volume removed. On failure the workspace is
        pushed to the DLQ for retry.
        """
        rows = await self.workspace_store.list_pending_archive()
        succeeded = failed = 0
        for workspace in rows:
            ok = await self._archive_workspace(workspace=workspace)
            if ok:
                succeeded += 1
            else:
                failed += 1
        return LifecycleResult(succeeded=succeeded, failed=failed)

    async def backup_active(self, *, now: datetime | None = None) -> LifecycleResult:
        """Write a daily ObjectStore snapshot for every active workspace."""
        rows = await self.workspace_store.list_active()
        when = now or datetime.now(UTC)
        date_str = when.strftime("%Y-%m-%d")
        succeeded = failed = 0
        for workspace in rows:
            ok = await self._backup_workspace(workspace=workspace, date_str=date_str)
            if ok:
                succeeded += 1
            else:
                failed += 1
        return LifecycleResult(succeeded=succeeded, failed=failed)

    async def drain_dlq(self, *, limit: int = 16) -> LifecycleResult:
        """Retry up to ``limit`` ready DLQ rows; oldest first."""
        rows = await self.dlq.take_ready(limit=limit, now=datetime.now(UTC))
        succeeded = failed = 0
        for row in rows:
            ok = await self._retry_dlq_row(row=row)
            if ok:
                succeeded += 1
            else:
                failed += 1
        return LifecycleResult(succeeded=succeeded, failed=failed)

    # ----- per-volume work --------------------------------------------

    async def _archive_workspace(self, *, workspace: UserWorkspace) -> bool:
        """Tar+gz the volume, upload, mark archived, remove the volume.

        Returns ``True`` on success. On any failure the workspace is
        enqueued to the DLQ and the caller is told ``False``.
        """
        try:
            payload = await self.docker.archive_volume(
                volume=workspace.volume_name,
                image=self.settings.sandbox_image,
                max_bytes=self.settings.workspace_archive_max_inflight_bytes,
            )
        except DockerError as exc:
            await self._enqueue_dlq(workspace=workspace, op_kind="archive", error=str(exc))
            return False

        key = _archive_key(self.settings.workspace_archive_prefix, workspace)
        try:
            await self._put_with_metadata(key=key, payload=payload, workspace=workspace)
        except ObjectStoreError as exc:
            await self._enqueue_dlq(workspace=workspace, op_kind="archive", error=str(exc))
            return False

        try:
            await self.workspace_store.mark_archived(
                workspace_id=workspace.id, archived_object_key=key
            )
        except (WorkspaceNotFoundError, ValueError) as exc:
            # The row was hard-deleted between sweep and mark, or someone
            # un-soft-deleted it under us. The archive object is durable;
            # log + skip retry.
            logger.warning(
                "volume_lifecycle.mark_archived_skipped workspace=%s reason=%s",
                workspace.id,
                exc,
            )
            return False

        # Volume removal is best-effort — failure to remove just leaves
        # disk attached; the archive_object_key is already durable.
        try:
            await self.docker.remove_volume(volume=workspace.volume_name)
        except DockerError as exc:
            logger.warning(
                "volume_lifecycle.remove_volume_failed volume=%s reason=%s",
                workspace.volume_name,
                exc,
            )

        await self._emit_audit(
            workspace=workspace,
            action=AuditAction.WORKSPACE_ARCHIVE,
            result=AuditResult.SUCCESS,
            object_key=key,
            payload_size=len(payload),
        )
        return True

    async def _backup_workspace(self, *, workspace: UserWorkspace, date_str: str) -> bool:
        """Snapshot an active workspace to the daily backup prefix."""
        try:
            payload = await self.docker.archive_volume(
                volume=workspace.volume_name,
                image=self.settings.sandbox_image,
                max_bytes=self.settings.workspace_archive_max_inflight_bytes,
            )
        except DockerError as exc:
            await self._enqueue_dlq(workspace=workspace, op_kind="backup", error=str(exc))
            return False

        key = _backup_key(self.settings.workspace_backup_prefix, workspace, date_str)
        try:
            await self._put_with_metadata(key=key, payload=payload, workspace=workspace)
        except ObjectStoreError as exc:
            await self._enqueue_dlq(workspace=workspace, op_kind="backup", error=str(exc))
            return False

        await self._emit_audit(
            workspace=workspace,
            action=AuditAction.WORKSPACE_BACKUP,
            result=AuditResult.SUCCESS,
            object_key=key,
            payload_size=len(payload),
        )
        return True

    async def _retry_dlq_row(self, *, row: VolumeDLQRow) -> bool:
        """Try to re-run a single DLQ row; update DLQ state on outcome."""
        # Refresh the workspace; it may have been hard-deleted under us.
        try:
            # InMemory store has no get-by-id, but resolve(tenant, user)
            # finds the row (deterministic key). The workspace_id will
            # match if no one re-created the row.
            workspace = await self.workspace_store.resolve(
                tenant_id=row.tenant_id, user_id=row.user_id
            )
        except Exception as exc:
            logger.warning("volume_lifecycle.dlq_resolve_failed row=%s reason=%s", row.id, exc)
            await self.dlq.mark_done(row_id=row.id)
            return False

        if workspace.id != row.workspace_id:
            # The original workspace was hard-deleted and replaced; the
            # DLQ entry no longer maps to a real row.
            await self.dlq.mark_done(row_id=row.id)
            return False

        try:
            if row.op_kind == "archive":
                success = await self._archive_workspace(workspace=workspace)
            else:
                date_str = datetime.now(UTC).strftime("%Y-%m-%d")
                success = await self._backup_workspace(workspace=workspace, date_str=date_str)
        except Exception as exc:
            await self._record_dlq_failure(row=row, error=str(exc))
            return False

        if success:
            await self.dlq.mark_done(row_id=row.id)
            return True
        # ``_archive_workspace`` / ``_backup_workspace`` already enqueued
        # a fresh DLQ row on failure; drop the old one to avoid
        # double-counting retries.
        await self.dlq.mark_done(row_id=row.id)
        return False

    # ----- helpers ----------------------------------------------------

    async def _put_with_metadata(
        self, *, key: str, payload: bytes, workspace: UserWorkspace
    ) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        await self.object_store.put(
            key,
            payload,
            content_type="application/gzip",
            metadata={
                "sha256": digest,
                "tenant_id": str(workspace.tenant_id),
                "user_id": str(workspace.user_id),
                "volume_name": workspace.volume_name,
            },
        )

    async def _enqueue_dlq(
        self,
        *,
        workspace: UserWorkspace,
        op_kind: Literal["archive", "backup"],
        error: str,
    ) -> None:
        await self.dlq.enqueue(
            tenant_id=workspace.tenant_id,
            user_id=workspace.user_id,
            workspace_id=workspace.id,
            volume_name=workspace.volume_name,
            op_kind=op_kind,
            error=error,
        )
        await self._emit_audit(
            workspace=workspace,
            action=(
                AuditAction.WORKSPACE_ARCHIVE
                if op_kind == "archive"
                else AuditAction.WORKSPACE_BACKUP
            ),
            result=AuditResult.ERROR,
            object_key=None,
            payload_size=None,
            reason=error,
        )

    async def _record_dlq_failure(self, *, row: VolumeDLQRow, error: str) -> None:
        now = datetime.now(UTC)
        next_retry = _next_retry_at(now=now, attempts_after_failure=row.attempts + 1)
        await self.dlq.record_failure(row_id=row.id, error=error, next_retry_at=next_retry)

    async def _emit_audit(
        self,
        *,
        workspace: UserWorkspace,
        action: AuditAction,
        result: AuditResult,
        object_key: str | None,
        payload_size: int | None,
        reason: str | None = None,
    ) -> None:
        details: dict[str, object] = {
            "user_id": str(workspace.user_id),
            "workspace_id": str(workspace.id),
            "volume_name": workspace.volume_name,
        }
        if object_key is not None:
            details["object_key"] = object_key
        if payload_size is not None:
            details["payload_size"] = payload_size
        await self.audit.write(
            AuditEntry(
                tenant_id=workspace.tenant_id,
                actor_type="system",
                actor_id=self.service_name,
                action=action,
                resource_type="user_workspace",
                resource_id=str(workspace.id),
                result=result,
                reason=reason,
                details=details,
            )
        )


__all__ = [
    "LifecycleResult",
    "VolumeLifecycleManager",
]
