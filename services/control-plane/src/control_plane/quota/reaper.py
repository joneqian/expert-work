"""Reservation reaper — Stream C.5 (subsystems/16 § 5.4 / § 6).

Reservations that never see a ``commit`` or ``release`` call (process
crash, client disconnect, network partition) would otherwise pin
``reserved_total`` budget forever. The reaper scans ``token_reservation``
on a periodic schedule, finds rows in ``RESERVED`` older than the
configured max age, and transitions them to ``EXPIRED`` while refunding
the monthly ledger.

Wiring (in :func:`control_plane.app.create_app`):

* Started from the FastAPI ``lifespan`` ``yield``.
* Stopped via :meth:`stop` from the ``finally`` branch.
* Per-cycle errors are caught and logged — the reaper never crashes
  the process — and increment the
  ``expert_work_control_plane_quota_reaper_cycle_errors_total`` counter so
  alerting fires on a sustained failure rate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from expert_work.common.observability import expert_work_counter
from expert_work.persistence.quota import TokenReservationStore
from expert_work.persistence.rls import bypass_rls_var, current_tenant_id_var
from expert_work.protocol import TokenReservationRecord

logger = logging.getLogger("expert_work.control_plane.quota.reaper")

# Periodic-loop failures. Monotonic — alerting keys off ``rate(...)`` to
# distinguish a one-off blip from a reaper that is wedged every cycle.
_reaper_cycle_errors = expert_work_counter(
    "expert_work_control_plane_quota_reaper_cycle_errors_total",
    "Reservation reaper cycles that ended in a caught exception.",
)

# Type alias for the optional ``on_expire`` hook — receives one
# reservation row and may perform side effects (audit emit, metric
# increment, …). Errors raised here are caught and logged so the
# reaper still releases the row.
OnExpireCallback = Callable[[TokenReservationRecord], Awaitable[None]]


class ReservationReaper:
    """Background task: scan + expire stale reservations."""

    def __init__(
        self,
        *,
        reservation_store: TokenReservationStore,
        max_age_s: int,
        interval_s: int,
        batch_size: int = 100,
        on_expire: OnExpireCallback | None = None,
    ) -> None:
        if max_age_s <= 0:
            msg = "max_age_s must be positive"
            raise ValueError(msg)
        if interval_s <= 0:
            msg = "interval_s must be positive"
            raise ValueError(msg)
        self._store = reservation_store
        self._max_age_s = max_age_s
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._on_expire = on_expire
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
        self._task = asyncio.create_task(self._loop(), name="quota-reservation-reaper")

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
        """Run a single reap pass; return the number of rows expired."""
        # Reaper bypasses RLS: it operates across all tenants and needs
        # to see RESERVED rows that the per-request app role would
        # otherwise filter out. Bypass also tells the application that
        # the connection is *intended* to skip the policy; in
        # production we additionally ``SET ROLE audit_reader`` (the
        # BYPASSRLS role from migration 0005), which the SQL store
        # handles when a connection is configured for it.
        token_bypass = bypass_rls_var.set(True)
        token_tenant = current_tenant_id_var.set(None)
        try:
            expired = await self._store.list_expired(
                max_age_seconds=self._max_age_s,
                limit=self._batch_size,
            )
        finally:
            current_tenant_id_var.reset(token_tenant)
            bypass_rls_var.reset(token_bypass)

        released = 0
        for row in expired:
            # Each release runs in its own tenant context because the
            # ledger update needs RLS to pass for the row's owning
            # tenant.
            token_tenant_release = current_tenant_id_var.set(row.tenant_id)
            token_bypass_release = bypass_rls_var.set(False)
            try:
                # Stream 9.5 — the reaper runs on every instance. ``expire_reserved``
                # is an atomic CAS (FOR UPDATE + ledger refund) that returns True
                # only for the instance that actually performed the transition, so
                # the refund, the ``on_expire`` side effect, and the count all fire
                # exactly once even when peers race the same stale row.
                won = await self._store.expire_reserved(
                    reservation_id=row.id, tenant_id=row.tenant_id
                )
                if not won:
                    # A peer reaper / a client commit-release closed it first.
                    logger.info(
                        "quota.reaper.reservation_already_closed",
                        extra={"reservation_id": str(row.id)},
                    )
                    continue
                if self._on_expire is not None:
                    try:
                        await self._on_expire(row)
                    except Exception:
                        logger.exception(
                            "quota.reaper.on_expire_failed",
                            extra={"reservation_id": str(row.id)},
                        )
                released += 1
            finally:
                current_tenant_id_var.reset(token_tenant_release)
                bypass_rls_var.reset(token_bypass_release)
        return released

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                released = await self.run_once()
                if released:
                    logger.info(
                        "quota.reaper.expired",
                        extra={"released_count": released},
                    )
            except Exception:
                # The reaper's own errors must never crash the
                # process. Log + count + continue; next tick retries.
                _reaper_cycle_errors.inc()
                logger.exception("quota.reaper.cycle_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                pass  # normal periodic wake-up
