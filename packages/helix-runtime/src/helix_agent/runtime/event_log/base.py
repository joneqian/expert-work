# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/events/store/base.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - Reshaped interface to match Helix-Agent ADR-0002 schema:
#       * thread_id stays; session_id added; tenant_id (UUID) added; trace_id added
#       * dropped DeerFlow's run_id / category / content / event_metadata / user_id
#         (DeerFlow's payload model is collapsed into our single JSONB `payload`)
#   - Renamed RunEventStore -> EventStore (no DeerFlow-specific framing)
#   - Algorithms (FOR UPDATE seq alloc, batch writes, content truncation) preserved
#     in db.py
# Last sync: 2026-05-11
# ============================================================

"""Abstract interface for event_log storage (ADR-0002 schema).

Implementations:
- :class:`helix_agent.runtime.event_log.memory.InMemoryEventStore` — tests
- :class:`helix_agent.runtime.event_log.db.DbEventStore` — production (Postgres)
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from helix_agent.protocol import EventRecord, EventType


class EventStore(abc.ABC):
    """Append-only event_log storage interface.

    Contracts guaranteed by every implementation:

    1. ``put()`` events are retrievable via subsequent ``list_events()``.
    2. ``seq`` is strictly monotonic within the same ``thread_id``.
    3. Inserts violating ``UNIQUE(thread_id, seq)`` raise the underlying
       integrity error (Postgres ``UniqueViolation``).
    4. ``list_events()`` returns rows in ascending ``seq`` order.
    """

    @abc.abstractmethod
    async def put(
        self,
        *,
        thread_id: UUID,
        tenant_id: UUID,
        event_type: EventType | str,
        payload: dict[str, Any] | None = None,
        session_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> EventRecord:
        """Append one event, auto-assign ``seq``, return the complete record."""

    @abc.abstractmethod
    async def put_batch(self, events: Sequence[EventRecord]) -> list[EventRecord]:
        """Append multiple events sharing the same ``thread_id``.

        Used by the orchestrator's flush buffer (high-throughput path).
        All records in ``events`` must have the same ``thread_id``; ``seq``
        is auto-assigned (input ``seq`` is ignored).
        Returns the records with ``id`` / ``seq`` / ``created_at`` populated.
        """

    @abc.abstractmethod
    async def list_events(
        self,
        thread_id: UUID,
        *,
        event_types: Sequence[EventType | str] | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        limit: int = 500,
    ) -> list[EventRecord]:
        """Return events for ``thread_id`` in ascending ``seq`` order.

        Bidirectional cursor pagination:

        - ``after_seq``: first ``limit`` records with ``seq > after_seq``
        - ``before_seq``: last ``limit`` records with ``seq < before_seq``
        - neither: latest ``limit`` records
        """

    @abc.abstractmethod
    async def count(self, thread_id: UUID) -> int:
        """Total event count for ``thread_id``."""

    @abc.abstractmethod
    async def delete_by_thread(self, thread_id: UUID) -> int:
        """Delete every event for ``thread_id``; return rows removed.

        Tests / dev only — production access goes through retention TTL
        (Stream D.4 / G.8). Append-only DB role does not grant DELETE.
        """
