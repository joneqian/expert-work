"""Abstract ``MemoryStore`` repository — Stream J.3 long-term memory.

Implementations:
- :class:`helix_agent.persistence.memory.memory.InMemoryMemoryStore`
- :class:`helix_agent.persistence.memory.sql.SqlMemoryStore`

The store deals in *vectors* — embedding text into a vector is the
caller's job (Stream J.3 PR2 wires the embedder). This keeps the
persistence layer free of any embedding-model dependency.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from helix_agent.common.threat_patterns import ThreatFinding
from helix_agent.protocol import MemoryItem


class MemoryInjectionBlockedError(Exception):
    """Capability Uplift Sprint #2 (Mini-ADR U-3) — write rejected.

    Raised by :meth:`MemoryStore.write` when any item's ``content``
    matches the ``strict`` threat-pattern set. The batch is rejected
    atomically (no partial writes) — see
    ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 3.2.

    Carries the per-item finding set so callers can emit one audit row
    per blocked item with the right caller-side context (API actor_id,
    writeback thread_id, DLQ row id).
    """

    def __init__(
        self,
        blocked: list[tuple[UUID, list[ThreatFinding]]],
    ) -> None:
        super().__init__(f"{len(blocked)} memory item(s) blocked by strict threat scan")
        self.blocked = blocked


class MemoryStore(abc.ABC):
    """Cross-session memory repository, scoped to ``(tenant_id, user_id)``."""

    @abc.abstractmethod
    async def write(self, items: Sequence[MemoryItem]) -> None:
        """Persist long-term memories. Each item carries its own ``id``
        and ``embedding``."""

    @abc.abstractmethod
    async def retrieve(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        query_embedding: Sequence[float],
        query_text: str | None = None,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        """Return the user's ``limit`` memories nearest ``query_embedding``,
        closest first. Soft-deleted rows are excluded (Stream K.K6).

        Capability Uplift Sprint #6 (Mini-ADR U-5): when ``query_text``
        is ``None`` the retrieval is pure-vector (cosine distance against
        the pgvector column) — the pre-Sprint-#6 behavior, kept for
        backward compatibility. When ``query_text`` is a non-empty string
        the retrieval is **hybrid**: vector recall is fused with Postgres
        full-text recall via Reciprocal Rank Fusion (``k=60``). Empty /
        whitespace-only ``query_text`` degrades to the vector path."""

    @abc.abstractmethod
    async def list_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """Stream K.K6 — list a user's live memories, newest first.

        Soft-deleted rows are filtered out so the UI / API caller never
        sees a forgotten memory. ``kind`` optionally narrows. The
        ``embedding`` field is still populated (callers may project it
        away at the API boundary)."""

    @abc.abstractmethod
    async def list_all_tenants(
        self,
        *,
        kind: Literal["fact", "episodic"] | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """Cross-tenant memory list — Stream N (Mini-ADR N-4).

        Caller MUST be inside ``bypass_rls_session()``. No ``user_id``
        filter — the platform admin view aggregates every user's
        memories across every tenant. Soft-deleted rows are excluded,
        newest first.
        """

    @abc.abstractmethod
    async def update_content(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
        content: str,
        embedding: Sequence[float],
        kind: Literal["fact", "episodic"] | None = None,
    ) -> MemoryItem | None:
        """Stream K.K6 — rewrite a live memory's content / kind.

        The caller must re-embed before calling — the store does not
        own an embedder. Soft-deleted rows are not updatable; returns
        ``None`` for unknown id / wrong tenant / wrong user / already
        soft-deleted."""

    @abc.abstractmethod
    async def soft_delete(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        memory_id: UUID,
    ) -> bool:
        """Stream K.K6 — stamp ``deleted_at`` (the forget action).

        Idempotent: returns ``True`` even when already deleted. Returns
        ``False`` for unknown id / wrong tenant / wrong user."""
