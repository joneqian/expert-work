"""Cross-replica per-workspace write lock — Stream TE-8.

Workspace file writes (``write_file``) and ``bash`` must not interleave
across orchestrator replicas that mount the *same* per-user workspace
volume (Docker named volumes can be mounted by several sandboxes at once,
the supervisor is multi-replica, and there is no DB-level workspace lease).
A per-process ``asyncio.Lock`` is therefore insufficient.

TE-ADR-3 (2026-06-05 复议) — the lock is **per-workspace, exclusive, for
writes only**:

- ``bash`` has no path argument and can touch any file, so a per-path lock
  could never cover it (PG advisory locks are keyed by an exact hash and
  have no shared mode). A single per-workspace key — ``{tenant}:{user}`` —
  makes ``bash`` and every file write mutually exclusive, which is correct
  and simple. The cost (same-user writes to *different* files serialise
  across replicas) is negligible: same-turn writes already serialise via
  the L.L6 scheduler, and a per-user workspace has very low write
  concurrency.
- **Reads take no lock.** ``write_file`` writes atomically (temp file +
  ``os.replace``), so a reader always sees a complete old-or-new snapshot.
  That is what lets reads run lock-free despite PG advisory locks having no
  shared-read mode.

This module defines the orchestrator-side contract only. The Postgres
implementation (``pg_advisory_xact_lock``) lives in the control plane, which
owns the database engine; the orchestrator tool layer depends on the
:class:`WorkspaceLock` Protocol and defaults to the no-op
:class:`NullWorkspaceLock`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class WorkspaceLock(Protocol):
    """Exclusive per-workspace write lock (Stream TE-8).

    Implementations serialise writes to one user's workspace across
    replicas for the lifetime of the returned context manager. A
    ``user_id`` of ``None`` denotes an ephemeral (non-shared) workspace and
    implementations may treat it as a no-op."""

    def acquire(
        self, *, tenant_id: UUID | None, user_id: UUID | None
    ) -> AbstractAsyncContextManager[None]:
        """Hold the workspace write lock for the duration of the ``async with``
        block. Released (and any backing transaction committed/rolled back)
        on exit, including on cancellation."""


class NullWorkspaceLock:
    """No-op :class:`WorkspaceLock` — single process / unit tests.

    Provides **no** cross-replica guarantee; production wires a real
    :class:`WorkspaceLock` (Postgres advisory lock) instead."""

    @asynccontextmanager
    async def acquire(self, *, tenant_id: UUID | None, user_id: UUID | None) -> AsyncIterator[None]:
        yield


@dataclass
class RecordingWorkspaceLock:
    """Recording :class:`WorkspaceLock` for tests.

    Records each ``(tenant_id, user_id)`` acquisition and tracks how many
    holders are currently inside the context (``active``) so a test can assert
    the guarded work ran *while the lock was held*."""

    acquired: list[tuple[UUID | None, UUID | None]] = field(default_factory=list)
    active: int = 0

    @asynccontextmanager
    async def acquire(self, *, tenant_id: UUID | None, user_id: UUID | None) -> AsyncIterator[None]:
        self.acquired.append((tenant_id, user_id))
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
