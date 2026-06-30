"""Abstract :class:`PlatformToolBudgetConfigStore` — Phase 3.

Single-row singleton storing the platform-global on/off for the
tool-output-budget feature. Tenant-less (platform-global), so SQL callers MUST
be inside ``bypass_rls_session()`` — no per-tenant RLS scope, exactly like
``platform_judge_config``.

An absent row means "not configured" → the service falls back to the
``HELIX_TOOL_OUTPUT_BUDGET`` env default.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformToolBudgetConfigRow:
    """The platform's tool-output-budget on/off (non-secret)."""

    enabled: bool
    updated_by: str | None


class PlatformToolBudgetConfigStore(abc.ABC):
    """Persistence Protocol for the single-row platform tool-budget config."""

    @abc.abstractmethod
    async def get(self) -> PlatformToolBudgetConfigRow | None:
        """The singleton row, or None if not configured. SQL callers bypass RLS."""

    @abc.abstractmethod
    async def put(self, *, enabled: bool, updated_by: str | None) -> None:
        """Upsert the singleton row (last write wins). SQL callers bypass RLS."""
