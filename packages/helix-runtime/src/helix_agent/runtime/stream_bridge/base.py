# ============================================================
# Adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/runtime/stream_bridge/base.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - run_id typed as UUID (helix-agent uses UUID throughout)
#   - HEARTBEAT_SENTINEL / END_SENTINEL retained verbatim
# Last sync: 2026-05-11
# ============================================================

"""SSE stream bridge protocol.

Decouples orchestrator workers (producers) from FastAPI SSE endpoints
(consumers); structurally mirrors LangGraph Platform's Queue + StreamManager.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class StreamEvent:
    """Single stream event.

    Attributes:
        id: Monotonically increasing event ID (used as SSE ``id:`` field;
            supports ``Last-Event-ID`` reconnection on the client side).
        event: SSE event name, e.g. ``"metadata"`` / ``"updates"`` /
            ``"events"`` / ``"error"`` / ``"end"``.
        data: JSON-serialisable payload.
    """

    id: str
    event: str
    data: Any


HEARTBEAT_SENTINEL = StreamEvent(id="", event="__heartbeat__", data=None)
END_SENTINEL = StreamEvent(id="", event="__end__", data=None)


class StreamBridge(abc.ABC):
    """Abstract async pub/sub bus per ``run_id``."""

    @abc.abstractmethod
    async def publish(self, run_id: UUID, event: str, data: Any) -> None:
        """Enqueue a single event for ``run_id`` (producer side)."""

    @abc.abstractmethod
    async def publish_end(self, run_id: UUID) -> None:
        """Signal that no more events will be produced for ``run_id``."""

    @abc.abstractmethod
    def subscribe(
        self,
        run_id: UUID,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        """Async iterator yielding events for ``run_id`` (consumer side).

        - Yields :data:`HEARTBEAT_SENTINEL` when no event arrives within
          ``heartbeat_interval`` seconds (prevents proxies from closing
          idle connections).
        - Yields :data:`END_SENTINEL` exactly once after the producer
          calls :meth:`publish_end`; the iterator then terminates.
        """

    @abc.abstractmethod
    async def cleanup(self, run_id: UUID, *, delay: float = 0) -> None:
        """Release resources associated with ``run_id``.

        ``delay > 0`` gives late subscribers time to drain remaining events.
        """

    async def close(self) -> None:  # noqa: B027 — intentional no-op default
        """Release backend resources. Default is a no-op (memory backend)."""
