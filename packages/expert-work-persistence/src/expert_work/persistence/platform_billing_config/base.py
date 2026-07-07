"""Abstract :class:`PlatformBillingConfigStore` — Stream 12.4.

Single-row singleton holding platform billing toggles read by the offline
billing-rollup job. For now one flag: ``rollup_enabled`` (default true).
Tenant-less (platform-global), so SQL callers MUST be inside
``bypass_rls_session()`` — no per-tenant RLS scope, exactly like
``platform_judge_config``.

An absent row means "default" → rollup enabled.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformBillingConfigRow:
    """The platform's billing toggles."""

    rollup_enabled: bool
    updated_by: str | None


class PlatformBillingConfigStore(abc.ABC):
    """Persistence Protocol for the single-row platform billing config."""

    @abc.abstractmethod
    async def get(self) -> PlatformBillingConfigRow | None:
        """The singleton row, or None if never set. SQL callers bypass RLS."""

    @abc.abstractmethod
    async def put(self, *, rollup_enabled: bool, updated_by: str | None) -> None:
        """Upsert the singleton row (last write wins). SQL callers bypass RLS."""
