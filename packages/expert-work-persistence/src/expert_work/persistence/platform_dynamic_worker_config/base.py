"""Abstract :class:`PlatformDynamicWorkerConfigStore` — B3 PR2.

Single-row singleton storing the platform-global ``dynamic_worker`` limits:
``max_concurrent``, ``max_per_run``, ``max_iterations``. Tenant-less
(platform-global), so SQL callers MUST be inside ``bypass_rls_session()`` —
no per-tenant RLS scope, exactly like ``platform_tool_budget_config``.

An absent row means "not configured" → the platform falls back to its
built-in defaults.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformDynamicWorkerConfigRow:
    """The platform's dynamic-worker limits (non-secret)."""

    max_concurrent: int
    max_per_run: int
    max_iterations: int
    updated_by: str | None


class PlatformDynamicWorkerConfigStore(abc.ABC):
    """Persistence Protocol for the single-row platform dynamic-worker config."""

    @abc.abstractmethod
    async def get(self) -> PlatformDynamicWorkerConfigRow | None:
        """The singleton row, or None if not configured. SQL callers bypass RLS."""

    @abc.abstractmethod
    async def put(
        self, *, max_concurrent: int, max_per_run: int, max_iterations: int, updated_by: str | None
    ) -> None:
        """Upsert the singleton row (last write wins). SQL callers bypass RLS."""
