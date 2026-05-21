"""``QuotaEnforcer`` — per-workspace volume quota gate (Stream J.15-补强-1).

STREAM-J-DESIGN § 9.5.1 + Mini-ADR J-29 第 1 项. The supervisor calls
:meth:`check` at acquire time (after the workspace row is resolved, before
the docker launch) to reject acquires that would exceed the
``size_limit_bytes`` ceiling; and :meth:`refresh_size` fire-and-forget
at release time to write the latest ``du`` back into the store so the
next check has a fresh basis.

Design notes:

* Acquire-time check is a **read** of ``workspace.size_bytes`` — no
  filesystem walk on the hot path. The freshness of that value depends
  on :meth:`refresh_size` being scheduled after each release.
* :meth:`refresh_size` swallows all errors — it's best-effort. The
  worst case is the next acquire sees a stale value, which is exactly
  the failure mode the OS-level ENOSPC fallback (Mini-ADR J-29 第 3 项,
  host LUKS / cloud-disk quota) is there to catch.
* Soft-deleted workspaces (``deleted_at IS NOT NULL``) are rejected at
  :meth:`check` time — Mini-ADR J-36 lifecycle 第 2 档 says a soft-
  deleted workspace's acquire is rejected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from helix_agent.persistence import UserWorkspaceStore, WorkspaceNotFoundError
from helix_agent.protocol import AuditEntry, UserWorkspace
from helix_agent.protocol.audit import AuditAction, AuditResult
from sandbox_supervisor.docker_client import DockerClient, DockerError
from sandbox_supervisor.domain import (
    WorkspaceDeletedError,
    WorkspaceQuotaExceededError,
)

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    """Structural protocol matching :class:`SandboxSupervisor.AuditSink`.

    Both sites duck-type the same shape; we don't share an import to
    avoid a circular dep (supervisor.py imports this module).
    """

    async def write(self, entry: AuditEntry) -> None: ...


@dataclass(frozen=True)
class QuotaEnforcer:
    """Acquire-time gate + post-release size refresh for J.15 workspaces.

    Instantiated once per supervisor; ``service_name`` is stamped into
    the audit entries' ``actor_id`` field so the trail names the
    supervisor process (matching the pattern in
    :meth:`SandboxSupervisor._emit_audit`).
    """

    workspace_store: UserWorkspaceStore
    audit: AuditSink
    docker: DockerClient
    measure_image: str
    service_name: str

    async def check(self, *, workspace: UserWorkspace) -> None:
        """Reject the acquire if quota is exceeded or the workspace is soft-deleted.

        Raises:
            WorkspaceDeletedError: ``workspace.deleted_at`` is set.
                Mini-ADR J-36 — soft-deleted workspaces don't accept
                new acquires; recovery is a separate operator action.
            WorkspaceQuotaExceededError:
                ``workspace.size_bytes >= workspace.size_limit_bytes``.
                Mini-ADR J-29 第 1 项 — control-plane translates to HTTP 429.

        On reject the enforcer emits the corresponding ``workspace:*``
        audit action before raising.
        """
        if workspace.deleted_at is not None:
            await self._emit(
                action=AuditAction.WORKSPACE_QUOTA_DENIED,
                workspace=workspace,
                reason="workspace is soft-deleted",
                details={"deleted_at": workspace.deleted_at.isoformat()},
            )
            raise WorkspaceDeletedError(workspace.id)
        if workspace.size_bytes >= workspace.size_limit_bytes:
            await self._emit(
                action=AuditAction.WORKSPACE_QUOTA_DENIED,
                workspace=workspace,
                reason=(f"size {workspace.size_bytes} >= limit {workspace.size_limit_bytes} bytes"),
                details={
                    "size_bytes": workspace.size_bytes,
                    "size_limit_bytes": workspace.size_limit_bytes,
                },
            )
            raise WorkspaceQuotaExceededError(
                workspace.id, workspace.size_bytes, workspace.size_limit_bytes
            )

    async def refresh_size(self, *, workspace: UserWorkspace) -> None:
        """Measure the volume's current size and write it back to the store.

        Best-effort: any failure (docker missing, volume gone, store row
        gone) is logged and swallowed. Designed to be scheduled
        fire-and-forget after a release so the next acquire's check has
        a fresh basis.
        """
        try:
            size_bytes = await self.docker.measure_volume_size(
                volume=workspace.volume_name,
                image=self.measure_image,
            )
        except DockerError:
            logger.warning(
                "quota_enforcer.refresh_size_measure_failed workspace=%s volume=%s",
                workspace.id,
                workspace.volume_name,
                exc_info=True,
            )
            return
        try:
            await self.workspace_store.update_size(workspace_id=workspace.id, size_bytes=size_bytes)
        except WorkspaceNotFoundError:
            # Row was hard-deleted between resolve and refresh — fine.
            logger.info("quota_enforcer.refresh_size_workspace_gone workspace=%s", workspace.id)

    async def _emit(
        self,
        *,
        action: AuditAction,
        workspace: UserWorkspace,
        reason: str,
        details: dict[str, object],
    ) -> None:
        await self.audit.write(
            AuditEntry(
                tenant_id=workspace.tenant_id,
                actor_type="system",
                actor_id=self.service_name,
                action=action,
                resource_type="user_workspace",
                resource_id=str(workspace.id),
                result=AuditResult.DENIED,
                reason=reason,
                details={"user_id": str(workspace.user_id), **details},
            )
        )


__all__ = ["QuotaEnforcer"]
