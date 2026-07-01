# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/persistence/thread_meta/base.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - tenant_id (UUID) is a required first-class parameter; DeerFlow's
#     three-state user_id AUTO sentinel collapsed to "tenant_id required"
#     (Stream C wires the contextvar at call sites instead)
#   - Returns Pydantic ThreadMeta (helix-agent-protocol) instead of dict
#   - Dropped DeerFlow-specific fields: assistant_id / display_name /
#     metadata_json (we may add them later if a use case appears)
#   - check_access semantics simplified: 1 mode (existing-or-bypass) instead
#     of DeerFlow's permissive / strict pair
# Last sync: 2026-05-11
# ============================================================

"""Abstract ``ThreadMetaStore`` repository — ADR-0002 schema.

Implementations:
- :class:`helix_agent.persistence.thread_meta.memory.InMemoryThreadMetaStore`
- :class:`helix_agent.persistence.thread_meta.sql.SqlThreadMetaStore`
"""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import ThreadMeta, ThreadStatus


class ThreadMetaStore(abc.ABC):
    """Per-thread metadata repository.

    Every mutating / read method takes ``tenant_id`` explicitly. There is
    no AUTO sentinel — Stream C wires tenant context at the call site
    (Control Plane handlers, Orchestrator worker pickup, etc.).
    """

    @abc.abstractmethod
    async def create(
        self,
        *,
        thread_id: UUID,
        tenant_id: UUID,
        created_by: str,
        user_id: UUID | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
    ) -> ThreadMeta:
        """Insert a new thread; ``thread_id`` must be unique.

        ``user_id`` records the owning :class:`~helix_agent.protocol.TenantUser`
        (Stream J.14). ``None`` for machine-triggered threads with no
        per-user instance.
        """

    @abc.abstractmethod
    async def get(self, thread_id: UUID, *, tenant_id: UUID) -> ThreadMeta | None:
        """Read a thread, filtered to ``tenant_id``.

        Returns ``None`` when the row does not exist or belongs to a
        different tenant — never reveals cross-tenant existence.
        """

    @abc.abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        status: ThreadStatus | None = None,
        user_id: UUID | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
        nonempty: bool = False,
        q: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadMeta]:
        """Paginated list of threads for ``tenant_id``, newest first.

        When ``user_id`` is given, only that user's threads are returned
        (Stream J.14 per-user scoping). ``agent_name`` / ``agent_version``
        narrow to threads bound to that agent (Stream H.6 Mini-ADR H-10 —
        feeds the per-agent Runs tab's thread-window resolve step).

        ``nonempty`` filters out threads that never had a run — the empty
        throwaway sessions eager thread-creation used to leave behind. Applied
        in the query so pagination counts only real threads. (An in-memory
        backend with no run store treats it as a no-op.)

        ``q`` is a case-insensitive substring match on ``title`` (the
        session-history search box). ``include_archived`` controls the
        soft-deleted ``ARCHIVED`` threads: when ``False`` (default) they are
        excluded — UNLESS ``status`` explicitly asks for ``ARCHIVED``, which
        always wins (an explicit status is an exact filter).
        """

    @abc.abstractmethod
    async def list_all_tenants(
        self,
        *,
        status: ThreadStatus | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
        nonempty: bool = False,
        q: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ThreadMeta]:
        """Cross-tenant thread list — Stream N (Mini-ADR N-4).

        Caller MUST be inside ``bypass_rls_session()``. No ``user_id``
        filter — the platform admin view aggregates every user's
        sessions across every tenant. Newest first. ``agent_name`` /
        ``agent_version`` as in :meth:`list_by_tenant` (Mini-ADR H-10).
        ``nonempty`` / ``q`` / ``include_archived`` as in
        :meth:`list_by_tenant`.
        """

    @abc.abstractmethod
    async def update_title(
        self,
        thread_id: UUID,
        title: str,
        *,
        tenant_id: UUID,
    ) -> bool:
        """Set the human ``title``; returns True if a row matched the tenant.

        Used both for auto-titling (first user message) and manual rename.
        """

    @abc.abstractmethod
    async def update_status(
        self,
        thread_id: UUID,
        status: ThreadStatus,
        *,
        tenant_id: UUID,
    ) -> bool:
        """Update status; returns True if a row matched the tenant filter."""

    @abc.abstractmethod
    async def check_access(self, thread_id: UUID, tenant_id: UUID) -> bool:
        """``True`` iff the thread exists and belongs to ``tenant_id``."""

    @abc.abstractmethod
    async def delete(self, thread_id: UUID, *, tenant_id: UUID) -> bool:
        """Delete the thread; returns True if a row matched the tenant filter.

        Test / admin only. Production retention is driven by Stream D.3 TTL job.
        """
