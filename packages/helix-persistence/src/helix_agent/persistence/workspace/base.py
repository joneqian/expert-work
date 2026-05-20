"""Abstract ``UserWorkspaceStore`` repository — Stream J.15.

Implementations:
- :class:`helix_agent.persistence.workspace.memory.InMemoryUserWorkspaceStore`
- :class:`helix_agent.persistence.workspace.sql.SqlUserWorkspaceStore`
"""

from __future__ import annotations

import abc
from datetime import datetime
from uuid import UUID

from helix_agent.protocol import UserWorkspace


def workspace_volume_name(tenant_id: UUID, user_id: UUID) -> str:
    """Return the docker named-volume identifier for a ``(tenant, user)`` pair.

    Deterministic — the same pair always maps to the same volume, so a
    ``resolve()`` after a row already exists never has to reconcile a
    name. The id components are plain UUIDs (no secret), and an
    ``helix-ws-`` prefix makes the volume self-describing in
    ``docker volume ls``.
    """
    return f"helix-ws-{tenant_id}-{user_id}"


class WorkspaceNotFoundError(KeyError):
    """Raised when an op targets a ``user_workspace`` row that doesn't exist."""


class UserWorkspaceStore(abc.ABC):
    """Per-user persistent-workspace registry, scoped to ``(tenant_id, user_id)``.

    Supervisor-owned — there is no RLS on ``user_workspace``; the tenant
    and user are passed explicitly and scoping is application-layer
    (Mini-ADR J-1).
    """

    @abc.abstractmethod
    async def resolve(self, *, tenant_id: UUID, user_id: UUID) -> UserWorkspace:
        """Return the workspace for ``(tenant_id, user_id)``, creating it if absent.

        Idempotent upsert keyed by ``(tenant_id, user_id)``. The
        ``volume_name`` is deterministic (:func:`workspace_volume_name`),
        so a repeat call never changes it; ``last_accessed_at`` is
        bumped to *now* on every call.

        Soft-deleted rows (``deleted_at IS NOT NULL``) are still returned
        — soft-delete enforcement is a supervisor-layer concern. Callers
        must check ``workspace.deleted_at`` before acting on the row.
        ``last_accessed_at`` is **not** bumped for soft-deleted rows
        (resolve becomes a pure read).
        """

    @abc.abstractmethod
    async def update_size(self, *, workspace_id: UUID, size_bytes: int) -> None:
        """Set ``size_bytes`` to the latest measurement (Mini-ADR J-29 第 1 项).

        Called by :class:`QuotaEnforcer.refresh_size` after a fresh
        ``du`` inside the mounted container. The supervisor's
        ``release()`` runs this fire-and-forget so the exec hot path
        isn't blocked. ``size_bytes`` must be ``>= 0``; pass an int.

        Raises :class:`WorkspaceNotFoundError` if ``workspace_id``
        doesn't exist.
        """

    @abc.abstractmethod
    async def soft_delete(self, *, workspace_id: UUID, now: datetime) -> None:
        """Mark a workspace soft-deleted (Mini-ADR J-36 lifecycle 第 2 档).

        Sets ``deleted_at = now``. Idempotent: a second soft_delete is
        a no-op (keeps the original timestamp). The reaper picks the
        row up on its next sweep, runs the archive job, then calls
        :meth:`mark_archived` to advance the lifecycle.

        Raises :class:`WorkspaceNotFoundError` if ``workspace_id``
        doesn't exist.
        """

    @abc.abstractmethod
    async def mark_archived(self, *, workspace_id: UUID, archived_object_key: str) -> None:
        """Record the ObjectStore key of the tar.zst archive (Mini-ADR J-36 第 3 档).

        Called by the reaper after the archive job uploads the tar.zst
        and physically removes the docker volume. The row stays in the
        table until hard-delete (90 days, retention-cleanup-job — J.15-
        补强-2). Idempotent.

        The row must already be soft-deleted (CHECK constraint
        ``user_workspace_archive_consistency``); the SQL layer rejects
        archiving an active row at the DB level. Caller responsibility
        to soft_delete first.

        Raises :class:`WorkspaceNotFoundError` if ``workspace_id``
        doesn't exist.
        """

    @abc.abstractmethod
    async def list_pending_archive(self) -> list[UserWorkspace]:
        """Return soft-deleted workspaces whose archive job hasn't run yet.

        Equivalent SQL filter:
        ``deleted_at IS NOT NULL AND archived_object_key IS NULL``.
        Backed by the partial index
        ``user_workspace_pending_archive_idx`` for constant-time scans
        as the active table grows.
        """
