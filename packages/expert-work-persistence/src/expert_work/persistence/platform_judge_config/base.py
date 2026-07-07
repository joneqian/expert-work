"""Abstract :class:`PlatformJudgeConfigStore` — Stream PI-3-A1.

Single-row singleton storing the platform's chosen output/action **judge**
provider+model (non-secret). Tenant-less (platform-global), so SQL callers
MUST be inside ``bypass_rls_session()`` — no per-tenant RLS scope, exactly
like ``platform_embedding_config``.

An absent row means "not configured" → the judge falls back to each agent's
own primary model.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformJudgeConfigRow:
    """The platform's judge-model selection (non-secret)."""

    judge_provider: str | None
    judge_model: str | None
    updated_by: str | None


class PlatformJudgeConfigStore(abc.ABC):
    """Persistence Protocol for the single-row platform judge config."""

    @abc.abstractmethod
    async def get(self) -> PlatformJudgeConfigRow | None:
        """The singleton row, or None if not configured. SQL callers bypass RLS."""

    @abc.abstractmethod
    async def put(
        self,
        *,
        judge_provider: str | None,
        judge_model: str | None,
        updated_by: str | None,
    ) -> None:
        """Upsert the singleton row (last write wins). SQL callers bypass RLS."""
