"""Rollback monitor (Stream SE, SE-7d-3b-i) — periodic regression sweep.

Background worker that periodically scans every ACTIVE *distilled* skill version
across tenants and runs it through the SE-7d-3a :class:`RollbackGate`: a version
whose post-promotion success rate regressed below its promote-time baseline (or
the absolute floor) is auto-archived + feeds the breaker (SE-A11).

Mirrors the ``SkillEvolutionWorker`` shell (start / stop / periodic loop). The
sweep runs under ``_bypass_rls`` as the table OWNER — ``skill`` /
``skill_eval_result`` / ``skill_run_usage`` are all ENABLE-only (no FORCE), so
the owner reads + writes cross-tenant while each gate call still narrows to its
own ``tenant_id`` (see [memory:skill-curator-owner-rls-exemption]). The gate is
injected so the sweep logic stays unit-testable; the bypass GUC wiring + app
lifespan are real-path (integration).

Only *distilled* versions are swept: human-authored skills never auto-promoted,
so there is nothing to roll back. A version with no ``pass`` eval evidence is
skipped — there is no baseline to compare against, and the monitor never guesses.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from control_plane.skill_rollback import RollbackAction
from control_plane.skill_rollback_gate import RollbackGate
from helix_agent.common.observability import helix_counter
from helix_agent.persistence.rls import bypass_rls_var, current_tenant_id_var
from helix_agent.persistence.skill.base import SkillStore
from helix_agent.protocol.skill import SkillStatus

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


_rolled_back = helix_counter(
    "helix_control_plane_skill_rollback_total",
    "ACTIVE distilled skill versions auto-archived by the rollback monitor.",
)
_cycle_errors = helix_counter(
    "helix_control_plane_skill_rollback_cycle_errors_total",
    "Rollback monitor cycles that ended in a caught exception.",
)


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """Owner-scope the cross-tenant sweep (skill tables are ENABLE-only)."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@dataclass(frozen=True)
class RollbackMonitorConfig:
    window: timedelta = timedelta(days=7)  # rolling outcome window per version
    page_size: int = 100  # cross-tenant enumeration page


@dataclass(frozen=True)
class RollbackTally:
    """One sweep's accounting (observability + test assertions)."""

    scanned: int
    rolled_back: int
    kept: int
    insufficient: int
    skipped: int  # not a distilled target, or no promote baseline


@dataclass
class RollbackMonitor:
    """Periodic sweep: archive ACTIVE distilled versions that regressed."""

    skill_store: SkillStore
    gate: RollbackGate
    config: RollbackMonitorConfig = field(default_factory=RollbackMonitorConfig)
    clock: Callable[[], datetime] = _utcnow
    interval_s: int = 3600

    def __post_init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> RollbackTally:
        now = self.clock()
        since = now - self.config.window
        scanned = rolled_back = kept = insufficient = skipped = 0

        with _bypass_rls():
            async for skill in self._iter_active_skills():
                scanned += 1
                target = await self._resolve_target(skill)
                if target is None:
                    skipped += 1
                    continue
                version, agent_name, baseline = target
                decision = await self.gate.maybe_rollback(
                    skill_id=skill.id,
                    skill_version=version,
                    tenant_id=skill.tenant_id,  # type: ignore[arg-type] — non-None (resolve_target guards)
                    agent_name=agent_name,
                    promote_baseline=baseline,
                    since=since,
                    now=now,
                )
                if decision.action is RollbackAction.ROLLBACK:
                    rolled_back += 1
                    _rolled_back.inc()
                elif decision.action is RollbackAction.KEEP:
                    kept += 1
                else:
                    insufficient += 1

        return RollbackTally(
            scanned=scanned,
            rolled_back=rolled_back,
            kept=kept,
            insufficient=insufficient,
            skipped=skipped,
        )

    async def _iter_active_skills(self):
        cursor = None
        while True:
            page, cursor = await self.skill_store.list_skills_all_tenants(
                status=SkillStatus.ACTIVE, cursor=cursor, limit=self.config.page_size
            )
            for skill in page:
                yield skill
            if cursor is None:
                return

    async def _resolve_target(self, skill) -> tuple[int, str, float] | None:
        """Return ``(version, agent_name, promote_baseline)`` for a rollback-
        eligible ACTIVE distilled version, or ``None`` to skip."""
        if skill.tenant_id is None or skill.created_by_agent_name is None:
            return None  # platform / human skill — never an auto-promote target
        version_row = await self.skill_store.get_version_by_number(
            skill_id=skill.id, version=skill.latest_version, tenant_id=skill.tenant_id
        )
        if version_row is None or version_row.evolution_origin != "distilled":
            return None
        baseline = await self._promote_baseline(skill.id, skill.tenant_id, skill.latest_version)
        if baseline is None:
            return None  # no pass evidence → nothing to compare against
        return skill.latest_version, skill.created_by_agent_name, baseline

    async def _promote_baseline(self, skill_id, tenant_id, version) -> float | None:
        results = await self.skill_store.list_eval_results(skill_id=skill_id, tenant_id=tenant_id)
        for r in results:  # newest first
            if r.verdict == "pass" and r.skill_version == version:
                return r.skill_score
        return None

    # -------------------------------------------------- periodic loop (real path)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Schedule the periodic sweep. Idempotent."""
        if self.is_running:
            return
        self._task = asyncio.create_task(self._loop(), name="skill-rollback-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            # Expected: we just cancelled the loop task; awaiting it re-raises
            # the cancellation. Swallow it — clean shutdown, nothing to handle.
            pass
        finally:
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                tally = await self.run_once()
                if tally.rolled_back:
                    logger.info(
                        "skill_rollback_monitor.swept",
                        extra={"scanned": tally.scanned, "rolled_back": tally.rolled_back},
                    )
            except Exception:
                logger.exception("skill_rollback_monitor.cycle_failed")
                _cycle_errors.inc()
            await asyncio.sleep(self.interval_s)
