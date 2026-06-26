"""Stream 9.5 — distributed run-queue worker.

The synchronous SSE path executes a run inside the request handler's process.
The queue path decouples submission from execution: ``POST /runs`` with
``mode="queue"`` persists the run as ``status='queued'`` (durable, owned by no
process), and this worker — running in every control-plane instance — drains
the queue: it CAS-claims a queued run (``status='queued'`` → ``running`` +
ownership lease), rebuilds the agent, and executes it from the persisted
``enqueued_input``. The client reads the output over ``GET /runs/{id}/events``
(durable replay), so it need not hold the original connection.

Exactly-once across instances: the claim CAS (:meth:`RunStore.claim_queued`)
serialises competing workers so one wins each queued run. Horizontally
scalable: add instances → more drain throughput. Structurally a sibling of
:class:`OrphanSweep` (same lifespan loop + bypass-RLS scan + per-tenant spawn);
the two compose — this worker *starts* queued runs, the orphan sweep *recovers*
crashed ones (a worker that dies mid-run leaves a running orphan the sweep
reclaims from the checkpoint).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from control_plane.api.runs import build_run_graph_input
from control_plane.runtime import AgentRuntime
from helix_agent.common.observability import helix_counter
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.rls import (
    bypass_rls_var,
    current_tenant_id_var,
    current_user_id_var,
)
from helix_agent.persistence.thread_meta import ThreadMetaStore
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.runs import RunInfo, RunStatus, RunStore
from orchestrator import AgentFactoryError, run_agent

logger = logging.getLogger("helix.control_plane.run_queue_worker")

_dequeued_total = helix_counter(
    "helix_run_queue_dequeued_total",
    "Queued runs the run-queue worker claimed + started executing.",
)
_failed_total = helix_counter(
    "helix_run_queue_failed_total",
    "Queued runs the run-queue worker could not start, by reason.",
    ("reason",),
)


@contextmanager
def _bypass_rls() -> Iterator[None]:
    """RLS-bypass scope for the cross-tenant queue scan (reaper pattern)."""
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@contextmanager
def _tenant_scope(tenant_id: UUID, user_id: UUID | None = None) -> Iterator[None]:
    """Scope per-run work to the run's own tenant (+ user)."""
    tenant = current_tenant_id_var.set(tenant_id)
    bypass = bypass_rls_var.set(False)
    user = current_user_id_var.set(user_id)
    try:
        yield
    finally:
        current_user_id_var.reset(user)
        bypass_rls_var.reset(bypass)
        current_tenant_id_var.reset(tenant)


class RunQueueWorker:
    """In-process lifespan loop that drains the distributed run queue."""

    def __init__(
        self,
        *,
        run_store: RunStore,
        thread_store: ThreadMetaStore,
        agent_spec_store: AgentSpecStore,
        runtime: AgentRuntime,
        audit_logger: AuditLogger,
        approval_store: Any,
        interval_s: float = 2.0,
        batch_size: int = 10,
    ) -> None:
        self._runs = run_store
        self._threads = thread_store
        self._agents = agent_spec_store
        self._runtime = runtime
        self._audit = audit_logger
        self._approvals = approval_store
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="run-queue-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("run_queue_worker.cycle_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
            except TimeoutError:
                # Interval elapsed with no stop signal — run the next cycle.
                continue

    async def run_once(self) -> int:
        """Claim + start one batch of queued runs. Returns how many started."""
        with _bypass_rls():
            queued = await self._runs.list_queued(limit=self._batch_size)
        started = 0
        for run in queued:
            try:
                if await self._claim_and_start(run):
                    started += 1
            except Exception:
                logger.exception("run_queue_worker.start_failed", extra={"run_id": str(run.run_id)})
        return started

    async def _claim_and_start(self, run: RunInfo) -> bool:
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=self._runtime.run_manager.lease_ttl_s)
        with _bypass_rls():
            claimed = await self._runs.claim_queued(
                run_id=run.run_id,
                new_owner=self._runtime.run_manager.instance_id,
                lease_until=lease_until,
                heartbeat_at=now,
            )
        if claimed is None:
            # A peer claimed it first — the CAS guarantees exactly one winner.
            return False
        await self._execute(claimed)
        return True

    async def _fail(self, run: RunInfo, *, reason: str) -> None:
        with _tenant_scope(run.tenant_id):
            await self._runs.set_status(
                run_id=run.run_id,
                tenant_id=run.tenant_id,
                status=RunStatus.ERROR,
                updated_at=datetime.now(UTC),
                error=f"queued run could not start: {reason}",
                finished_at=datetime.now(UTC),
            )
        _failed_total.labels(reason=reason).inc()
        logger.warning("run_queue_worker.failed run_id=%s reason=%s", run.run_id, reason)

    async def _execute(self, run: RunInfo) -> None:
        """Rebuild the agent + execute a claimed queued run from its input."""
        with _tenant_scope(run.tenant_id, run.user_id):
            meta = await self._threads.get(run.thread_id, tenant_id=run.tenant_id)
            if meta is None or meta.agent_name is None or meta.agent_version is None:
                await self._fail(run, reason="no_agent")
                return
            record = await self._agents.get(
                tenant_id=run.tenant_id, name=meta.agent_name, version=meta.agent_version
            )
            if record is None:
                await self._fail(run, reason="agent_gone")
                return
            try:
                built = await self._runtime.get_agent(
                    tenant_id=run.tenant_id,
                    name=meta.agent_name,
                    version=meta.agent_version,
                    spec=record.spec,
                    user_id=str(run.user_id) if run.user_id is not None else None,
                )
            except AgentFactoryError:
                await self._fail(run, reason="unbuildable")
                return

            payload = run.enqueued_input or {}
            graph_input = build_run_graph_input(
                built,
                input_text=payload.get("input"),
                image_refs=list(payload.get("image_refs") or []),
                untrusted_content=payload.get("untrusted_content"),
                inputs=payload.get("inputs") or {},
            )

            # Adopt the durable run into THIS instance's registry (no new
            # agent_run row — claim_queued already flipped it to running).
            run_record = await self._runtime.run_manager.adopt(
                run_id=run.run_id,
                thread_id=run.thread_id,
                tenant_id=run.tenant_id,
                user_id=run.user_id,
            )
            run_record.bound_distilled_skills = built.bound_distilled_skills
            # ``adopt`` defaults is_resume=True (the orphan-sweep case resumes a
            # checkpoint). A queued run starts fresh with a real graph_input, so
            # it is NOT a durable resume — keep the resume histogram honest.
            run_record.is_resume = False

            configurable: dict[str, Any] = {
                "thread_id": str(run.thread_id),
                "tenant_id": str(run.tenant_id),
                "run_id": str(run.run_id),
            }
            if run.user_id is not None:
                configurable["user_id"] = str(run.user_id)
            if built.run_deadline_s > 0:
                configurable["deadline_at"] = time.monotonic() + float(built.run_deadline_s)
            config: RunnableConfig = {"configurable": configurable}

            worker = asyncio.create_task(
                run_agent(
                    bridge=self._runtime.stream_bridge,
                    run_manager=self._runtime.run_manager,
                    record=run_record,
                    graph=built.graph,  # type: ignore[arg-type]
                    graph_input=graph_input,
                    config=config,
                    audit_logger=self._audit,
                    approval_store=self._approvals,
                    event_store=self._runtime.run_event_store,
                    skill_run_usage_recorder=self._runtime.skill_run_usage_recorder,
                    trajectory_recorder=self._runtime.trajectory_recorder,
                    worker_spawn_budget=self._runtime.new_worker_spawn_budget(),
                    tool_replay_safe=built.tool_replay_safe,
                )
            )
            await self._runtime.run_manager.attach_task(run.run_id, worker)

        _dequeued_total.inc()
        logger.info(
            "run_queue_worker.started run_id=%s by=%s",
            run.run_id,
            self._runtime.run_manager.instance_id,
        )
