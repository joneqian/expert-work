"""``StoreSkillRunUsageRecorder`` — Stream SE (SE-7d-3b-ii).

Control-plane implementation of the :class:`SkillRunUsageRecorder` Protocol the
orchestrator depends on. At a run's terminal hook the orchestrator calls
:meth:`record` once per distilled skill version it bound; this writes the
``skill_run_usage`` row the SE-7d rollback monitor later aggregates.

Best-effort: any failure is logged + swallowed — a bookkeeping write must never
fail a user's run (same discipline as ``ThrottledActivityRecorder``). The clock
+ id factory are injected so the write is deterministic under test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.persistence.skill.base import SkillStore
from helix_agent.protocol import SkillRunUsage

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class StoreSkillRunUsageRecorder:
    """Writes ``skill_run_usage`` rows via the SkillStore. Errors swallowed."""

    store: SkillStore
    clock: Callable[[], datetime] = _utcnow
    id_factory: Callable[[], UUID] = field(default=uuid4)

    async def record(
        self,
        *,
        skill_id: UUID,
        skill_version: int,
        tenant_id: UUID,
        agent_name: str,
        thread_id: UUID,
        outcome: str,
    ) -> None:
        try:
            await self.store.record_skill_run_usage(
                usage=SkillRunUsage(
                    id=self.id_factory(),
                    tenant_id=tenant_id,
                    skill_id=skill_id,
                    skill_version=skill_version,
                    thread_id=thread_id,
                    agent_name=agent_name,
                    outcome=outcome,  # type: ignore[arg-type] — store validates the literal
                    created_at=self.clock(),
                )
            )
        except Exception:
            logger.warning("skill_run_usage.record_failed skill_id=%s", skill_id, exc_info=True)


__all__ = ["StoreSkillRunUsageRecorder"]
