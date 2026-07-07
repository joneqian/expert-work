"""``ThrottledActivityRecorder`` — Capability Uplift Sprint #4 (Mini-ADR U-27).

Activity tracking for the Curator state machine. Every ``agent build``
that binds a skill (via ``_load_skills``) and every ``skill_view`` tool
call should mark the skill as just-used so the daily Curator sweep
doesn't transition it to ``stale``.

Naive ``UPDATE skill SET last_used_at = NOW()`` on every call would
amplify writes to the skill row under high agent-build / skill_view
fan-out (e.g. 100 agent runs/sec * 5 skills each = 500 UPDATEs/sec on
a small skill set). The recorder dedupes per-skill writes to once per
``ttl_seconds`` window (default 1 hour) — that's plenty granular for a
state machine measured in days.

Process-local: each control-plane replica throttles independently. The
worst case under N replicas is N writes per skill per hour, which is
still negligible. Cross-process coordination via Redis would be tighter
but not worth the dependency for state-machine-grade time scales.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING
from uuid import UUID

from expert_work.common.uplift_metrics import record_curator_transition

if TYPE_CHECKING:
    from expert_work.persistence import SkillStore

logger = logging.getLogger("expert_work.control_plane.skill_activity")

# Cap the LRU at 10k entries — bounded memory even if a tenant has
# thousands of skills churning through agent builds. Eviction is a
# minor accuracy hit (an evicted skill may get a redundant UPDATE on
# its next bump) but never a correctness hit.
_LRU_CAP: int = 10_000

# Default 1 hour. State machine cares about days; 1 hour throttle
# leaves up to 1 hour of staleness in worst case (negligible against
# 30-day stale threshold).
_DEFAULT_TTL_SECONDS: int = 3600


class ThrottledActivityRecorder:
    """In-process per-skill throttle around ``SkillStore.bump_last_used_at``.

    Implements :class:`expert_work.common.skill_activity.SkillActivityRecorder`
    via ``record``; ``maybe_record`` exposes the same logic with a
    boolean return for tests that want to assert whether a SQL UPDATE
    actually fired.

    Thread-safety: uses an :class:`asyncio.Lock` to serialize the
    "check + mark" critical section. Fine under asyncio's single-thread
    model (one coroutine in the lock at a time); not safe under
    free-threaded Python without further work — but the control plane
    runs under the standard GIL'd asyncio loop.

    Process-local: under N control-plane replicas, worst case is N
    SQL UPDATEs per skill per ttl window. The Curator's day-scale
    decisions don't notice the difference.
    """

    def __init__(
        self,
        store: SkillStore,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        cap: int = _LRU_CAP,
    ) -> None:
        self._store = store
        self._ttl = ttl_seconds
        self._cap = cap
        self._last: OrderedDict[UUID, float] = OrderedDict()
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """Drop all cached timestamps — for tests that want a fresh
        throttle state without re-constructing the recorder."""
        self._last.clear()

    async def record(self, *, skill_id: UUID, tenant_id: UUID) -> None:
        """:class:`SkillActivityRecorder` Protocol entry — fire-and-forget
        from the agent hot path. Discards the boolean result; tests use
        :meth:`maybe_record` instead when they need to assert it."""
        await self.maybe_record(skill_id=skill_id, tenant_id=tenant_id)

    async def maybe_record(
        self,
        *,
        skill_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Bump ``last_used_at`` if the throttle window has elapsed.

        Returns ``True`` if a SQL UPDATE actually fired (used by tests +
        the metrics layer to count real activity vs. squashed dupes).
        Failures from the store are logged + swallowed — the agent hot
        path must NOT fail because activity tracking hiccuped.
        """
        now = time.monotonic()
        async with self._lock:
            last = self._last.get(skill_id)
            if last is not None and (now - last) < self._ttl:
                # Move to MRU end so eviction follows usage.
                self._last.move_to_end(skill_id)
                return False
            self._last[skill_id] = now
            self._last.move_to_end(skill_id)
            # LRU eviction once we're past cap.
            while len(self._last) > self._cap:
                self._last.popitem(last=False)

        try:
            updated, auto_revived = await self._store.bump_last_used_at(
                skill_id=skill_id, tenant_id=tenant_id
            )
        except Exception:
            # Per-skill activity is best-effort — never fail the agent
            # run because the audit / state-machine bookkeeping
            # tripped. Log so SecOps can spot pathological rates.
            logger.exception(
                "skill_activity.bump_failed skill_id=%s tenant_id=%s",
                skill_id,
                tenant_id,
            )
            return False

        if auto_revived:
            # Per-call auto-revive — single transition. The audit row
            # belongs to the caller (orchestrator) because they know
            # the actor; the metric is platform-scoped here.
            record_curator_transition(from_state="stale", to_state="active", count=1)
            logger.info(
                "skill_activity.auto_revived skill_id=%s tenant_id=%s",
                skill_id,
                tenant_id,
            )
        return updated
