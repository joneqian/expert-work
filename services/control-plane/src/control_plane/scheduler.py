"""Trigger scheduler — Stream J.10 (Mini-ADR J-26 / J-42).

A single-replica background worker inside the control-plane. Each
``run_once`` sweep does three passes:

1. **fire** — poll ``agent_trigger`` for enabled ``cron`` triggers
   whose ``croniter`` schedule has come due, and start a run for each.
2. **reconcile** — for every ``fired`` ``trigger_run``, read the linked
   ``agent_run`` outcome: success → ``succeeded``; failure → ``retrying``
   (with a backoff) or ``dead_letter`` once the attempt budget is spent.
3. **retry** — re-fire ``retrying`` ``trigger_run`` rows whose
   ``next_retry_at`` has passed (Mini-ADR J-26 (1), K.K7 DLQ pattern).

Mini-ADR J-42: the ``agent_trigger`` table is the single source of
truth (no APScheduler jobstore). Restart-safe — a long outage fires a
due trigger once, not once per missed slot.

Stream 9.5 — the two run-spawning passes are CAS-guarded so the scheduler
is safe to run on more than one instance: ``_fire_due_cron`` claims the due
slot via ``TriggerStore.claim_cron_fire`` (CAS on ``last_fired_at``) and
``_retry_due`` claims a retrying firing via ``TriggerRunStore.claim_retry``
(CAS ``retrying`` → ``fired``) — exactly one instance wins each, so blue+green
never double-spawn. The reconcile pass is idempotent (both instances derive the
same terminal status from the same run outcome). (Deployment still single-
replicas the scheduler today because the co-located ``SkillCurator`` shares the
``enable_scheduler`` gate and is not yet CAS-hardened; the guards here remove the
scheduler's own double-fire hazard and are the prerequisite for going wider.)

Wiring (in :func:`control_plane.app.create_app`): started from the
FastAPI ``lifespan`` ``yield``, stopped via :meth:`stop` from the
``finally`` branch — the same shape as :class:`ReservationReaper`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from croniter import croniter

from control_plane.agent_disable_status import AgentDisableService
from control_plane.runtime import AgentRuntime
from control_plane.tenant_status import TenantStatusService
from control_plane.trigger_firing import fire_trigger
from helix_agent.common.observability import helix_counter
from helix_agent.persistence import (
    ApprovalStore,
    ThreadMetaStore,
    TriggerRunStore,
    TriggerStore,
)
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.rls import (
    bypass_rls_var,
    current_tenant_id_var,
    current_user_id_var,
)
from helix_agent.persistence.tenant_config import TenantConfigStore
from helix_agent.protocol import TriggerRecord, TriggerRunRecord, TriggerRunStatus
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.runs import RunStatus, RunStore

logger = logging.getLogger("helix.control_plane.scheduler")

#: DLQ retry budget — after this many failed firings a trigger_run is
#: dead-lettered (K.K7 pattern, Mini-ADR J-26 (1)).
_MAX_ATTEMPTS = 5

#: Per-failure backoff before the next retry: 1m → 5m → 30m → 2h → 6h.
_BACKOFF_SECONDS: tuple[int, ...] = (60, 5 * 60, 30 * 60, 2 * 3600, 6 * 3600)

#: agent_run statuses that count as a failed firing (→ DLQ retry).
_FAILED_RUN_STATUSES = frozenset({RunStatus.ERROR, RunStatus.TIMEOUT})

_scheduler_cycle_errors = helix_counter(
    "helix_control_plane_trigger_scheduler_cycle_errors_total",
    "Trigger scheduler cycles that ended in a caught exception.",
)
_dead_letters = helix_counter(
    "helix_control_plane_trigger_dead_letters_total",
    "Trigger firings that exhausted the retry budget and were dead-lettered.",
)


def _next_fire(expr: str, after: datetime) -> datetime:
    """Next cron fire time strictly after ``after`` (raises on a bad expr)."""
    result: datetime = croniter(expr, after).get_next(datetime)
    return result


def _is_cron_due(trigger: TriggerRecord, *, now: datetime) -> bool:
    """Whether a cron trigger's next scheduled fire has come due.

    The base is ``last_fired_at`` (or ``created_at`` for a trigger that
    never fired). A malformed cron expression raises — the caller
    catches it per-trigger so one bad row never aborts the sweep.
    """
    expr = trigger.config.get("expr")
    if not isinstance(expr, str):
        msg = f"trigger {trigger.id} has no cron expr"
        raise ValueError(msg)
    base = trigger.last_fired_at or trigger.created_at
    return _next_fire(expr, base) <= now


def _backoff_for(attempt: int) -> int:
    """Seconds to wait before the retry that follows failure ``attempt``."""
    idx = min(max(attempt - 1, 0), len(_BACKOFF_SECONDS) - 1)
    return _BACKOFF_SECONDS[idx]


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for a cross-tenant store scan (reaper pattern)."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID, user_id: UUID | None = None) -> Iterator[None]:
    """Scope per-trigger work to the trigger's own tenant (+ user)."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    user = current_user_id_var.set(user_id)
    try:
        yield
    finally:
        current_user_id_var.reset(user)
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


class TriggerScheduler:
    """Background worker: fire due cron triggers + run the DLQ sweep."""

    def __init__(
        self,
        *,
        trigger_store: TriggerStore,
        trigger_run_store: TriggerRunStore,
        run_store: RunStore,
        agent_spec_store: AgentSpecStore,
        thread_store: ThreadMetaStore,
        runtime: AgentRuntime,
        audit_logger: AuditLogger,
        approval_store: ApprovalStore,
        interval_s: int,
        batch_size: int = 100,
        # Capability Uplift Sprint #1 — Mini-ADR U-2 Layer B.
        tenant_config_store: TenantConfigStore | None = None,
        # Stream RT-4 (RT-ADR-16) — kill-switch gate for scheduled fires.
        agent_disable_service: AgentDisableService | None = None,
        tenant_status_service: TenantStatusService | None = None,
    ) -> None:
        if interval_s <= 0:
            msg = "interval_s must be positive"
            raise ValueError(msg)
        self._triggers = trigger_store
        self._trigger_runs = trigger_run_store
        self._runs = run_store
        self._agents = agent_spec_store
        self._threads = thread_store
        self._runtime = runtime
        self._audit = audit_logger
        self._approvals = approval_store
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._tenant_config_store = tenant_config_store
        self._agent_disable_service = agent_disable_service
        self._tenant_status_service = tenant_status_service
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Schedule the periodic loop. Idempotent: re-calling is a no-op."""
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="trigger-scheduler")

    async def stop(self) -> None:
        """Signal stop + await the loop's clean exit."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._interval_s + 5)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None

    async def run_once(self) -> int:
        """One sweep — fire due cron triggers, reconcile, retry. Return
        the number of runs spawned (cron fires + retries)."""
        now = datetime.now(UTC)
        spawned = await self._fire_due_cron(now)
        await self._reconcile_fired()
        spawned += await self._retry_due(now)
        return spawned

    # -- pass 1: fire due cron triggers ----------------------------------

    async def _fire_due_cron(self, now: datetime) -> int:
        with _bypass_rls():
            triggers = await self._triggers.list_enabled_cron()
        fired = 0
        for trigger in triggers[: self._batch_size]:
            try:
                if not _is_cron_due(trigger, now=now):
                    continue
                if await self._fire_cron(trigger, now=now):
                    fired += 1
            except Exception:
                logger.exception("scheduler.trigger_failed", extra={"trigger_id": str(trigger.id)})
        return fired

    async def _fire_cron(self, trigger: TriggerRecord, *, now: datetime) -> bool:
        with _tenant_scope(trigger.tenant_id, trigger.user_id):
            # Stream 9.5 — CAS-claim the due slot before firing so blue+green
            # don't both spawn a run for the same tick. The claim stamps
            # ``last_fired_at`` (== exactly-once guard); the loser skips.
            won = await self._triggers.claim_cron_fire(
                trigger_id=trigger.id,
                tenant_id=trigger.tenant_id,
                expected_last_fired_at=trigger.last_fired_at,
                new_last_fired_at=now,
            )
            if not won:
                return False
            # The claim already stamped ``last_fired_at`` — don't double-write.
            run_id = await self._fire(trigger, now=now, stamp_last_fired=False)
            if run_id is None:
                return False
            await self._trigger_runs.create(
                TriggerRunRecord(
                    id=uuid4(),
                    tenant_id=trigger.tenant_id,
                    trigger_id=trigger.id,
                    run_id=run_id,
                    status=TriggerRunStatus.FIRED,
                    attempt=1,
                    triggered_at=now,
                )
            )
            return True

    async def _fire(
        self, trigger: TriggerRecord, *, now: datetime, stamp_last_fired: bool = True
    ) -> UUID | None:
        """Spawn a run for ``trigger`` — caller already set the tenant scope."""
        return await fire_trigger(
            trigger,
            now=now,
            agent_spec_store=self._agents,
            runtime=self._runtime,
            thread_store=self._threads,
            audit_logger=self._audit,
            approval_store=self._approvals,
            trigger_store=self._triggers,
            tenant_config_store=self._tenant_config_store,
            agent_disable_service=self._agent_disable_service,
            tenant_status_service=self._tenant_status_service,
            stamp_last_fired=stamp_last_fired,
        )

    # -- pass 2: reconcile fired firings against their run outcome -------

    async def _reconcile_fired(self) -> None:
        with _bypass_rls():
            rows = await self._trigger_runs.list_fired(limit=self._batch_size)
        now = datetime.now(UTC)
        for row in rows:
            try:
                await self._reconcile_one(row, now=now)
            except Exception:
                logger.exception(
                    "scheduler.reconcile_failed", extra={"trigger_run_id": str(row.id)}
                )

    async def _reconcile_one(self, row: TriggerRunRecord, *, now: datetime) -> None:
        if row.run_id is None:
            return
        with _tenant_scope(row.tenant_id):
            run = await self._runs.get(run_id=row.run_id, tenant_id=row.tenant_id)
            if run is None:
                return
            if run.status is RunStatus.SUCCESS:
                await self._trigger_runs.update(
                    row.model_copy(update={"status": TriggerRunStatus.SUCCEEDED})
                )
            elif run.status in _FAILED_RUN_STATUSES:
                await self._trigger_runs.update(self._after_failure(row, now=now, error=run.error))
            elif run.status is RunStatus.INTERRUPTED:
                # A deliberately-cancelled run is a terminal failure — no retry.
                await self._trigger_runs.update(
                    row.model_copy(
                        update={
                            "status": TriggerRunStatus.FAILED,
                            "error": "run interrupted",
                        }
                    )
                )
            # PAUSED / RUNNING / PENDING — not terminal; reconcile next sweep.

    def _after_failure(
        self, row: TriggerRunRecord, *, now: datetime, error: str | None
    ) -> TriggerRunRecord:
        """Transition a failed firing — ``retrying`` with a backoff, or
        ``dead_letter`` once the retry budget is spent."""
        if row.attempt >= _MAX_ATTEMPTS:
            _dead_letters.inc()
            logger.warning(
                "scheduler.dead_letter",
                extra={"trigger_run_id": str(row.id), "attempt": row.attempt},
            )
            return row.model_copy(update={"status": TriggerRunStatus.DEAD_LETTER, "error": error})
        return row.model_copy(
            update={
                "status": TriggerRunStatus.RETRYING,
                "next_retry_at": now + timedelta(seconds=_backoff_for(row.attempt)),
                "error": error,
            }
        )

    # -- pass 3: re-fire retrying firings whose backoff has elapsed ------

    async def _retry_due(self, now: datetime) -> int:
        with _bypass_rls():
            rows = await self._trigger_runs.list_due_retries(before=now, limit=self._batch_size)
        fired = 0
        for row in rows:
            try:
                if await self._retry_one(row, now=now):
                    fired += 1
            except Exception:
                logger.exception("scheduler.retry_failed", extra={"trigger_run_id": str(row.id)})
        return fired

    async def _retry_one(self, row: TriggerRunRecord, *, now: datetime) -> bool:
        with _tenant_scope(row.tenant_id):
            # Stream 9.5 — CAS-claim the retry (retrying → fired) so only one
            # instance re-fires it; a peer that won already flipped it out of
            # ``retrying`` and our claim returns False → skip (no duplicate run).
            if not await self._trigger_runs.claim_retry(
                trigger_run_id=row.id, tenant_id=row.tenant_id
            ):
                return False
            trigger = await self._triggers.get(trigger_id=row.trigger_id, tenant_id=row.tenant_id)
        if trigger is None or not trigger.enabled:
            # Trigger deleted / disabled while retrying — abandon it.
            with _tenant_scope(row.tenant_id):
                await self._trigger_runs.update(
                    row.model_copy(
                        update={
                            "status": TriggerRunStatus.FAILED,
                            "next_retry_at": None,
                        }
                    )
                )
            return False
        with _tenant_scope(trigger.tenant_id, trigger.user_id):
            run_id = await self._fire(trigger, now=now)
            if run_id is None:
                # Agent gone / un-buildable — terminal, no infinite loop.
                await self._trigger_runs.update(
                    row.model_copy(
                        update={
                            "status": TriggerRunStatus.FAILED,
                            "next_retry_at": None,
                        }
                    )
                )
                return False
            await self._trigger_runs.update(
                row.model_copy(
                    update={
                        "attempt": row.attempt + 1,
                        "run_id": run_id,
                        "status": TriggerRunStatus.FIRED,
                        "next_retry_at": None,
                    }
                )
            )
            return True

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                spawned = await self.run_once()
                if spawned:
                    logger.info("scheduler.swept", extra={"spawned_count": spawned})
            except Exception:
                logger.exception("scheduler.cycle_failed")
                _scheduler_cycle_errors.inc()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                # Normal periodic wake-up — the interval elapsed with no stop
                # signal, so loop round for the next sweep.
                pass


__all__ = ["TriggerScheduler"]
