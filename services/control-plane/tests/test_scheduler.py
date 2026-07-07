"""Unit tests for the J.10 trigger scheduler — Mini-ADR J-26 / J-42."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from control_plane.audit import build_default_audit_logger
from control_plane.runtime import AgentRuntime
from control_plane.scheduler import TriggerScheduler, _is_cron_due, _next_fire
from expert_work.persistence import (
    InMemoryApprovalStore,
    InMemoryThreadMetaStore,
    InMemoryTriggerRunStore,
    InMemoryTriggerStore,
)
from expert_work.persistence.agent_spec import InMemoryAgentSpecStore
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import (
    AgentSpec,
    TriggerRecord,
    TriggerRunRecord,
    TriggerRunStatus,
)
from expert_work.runtime.runs import DisconnectMode, InMemoryRunStore, RunInfo, RunStatus
from tests.agent_fixtures import stub_agent_runtime

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
_TENANT = uuid4()

_MANIFEST: dict[str, Any] = {
    "apiVersion": "expert_work.io/v1",
    "kind": "Agent",
    "metadata": {"name": "reporter", "version": "1.0.0", "tenant": "platform-eng"},
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you report"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _trigger(
    *,
    name: str = "nightly",
    expr: str = "0 9 * * *",
    last_fired_at: datetime | None = None,
    created_at: datetime = _BASE,
) -> TriggerRecord:
    return TriggerRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        agent_name="reporter",
        agent_version="1.0.0",
        name=name,
        kind="cron",
        config={"expr": expr, "seed_input": "go"},
        enabled=True,
        source="api",
        last_fired_at=last_fired_at,
        created_at=created_at,
        updated_at=created_at,
    )


async def _build_scheduler(
    *,
    trigger_store: InMemoryTriggerStore,
    trigger_run_store: InMemoryTriggerRunStore,
    run_store: InMemoryRunStore | None = None,
    seed_agent: bool = True,
) -> tuple[TriggerScheduler, AgentRuntime]:
    agents = InMemoryAgentSpecStore()
    if seed_agent:
        await agents.create(
            tenant_id=_TENANT,
            spec=AgentSpec.model_validate(_MANIFEST),
            spec_sha256="a" * 64,
            created_by="test",
        )
    runtime = stub_agent_runtime()
    scheduler = TriggerScheduler(
        trigger_store=trigger_store,
        trigger_run_store=trigger_run_store,
        run_store=run_store or InMemoryRunStore(),
        agent_spec_store=agents,
        thread_store=InMemoryThreadMetaStore(),
        runtime=runtime,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        approval_store=InMemoryApprovalStore(),
        interval_s=60,
    )
    return scheduler, runtime


def _run_info(run_id: UUID, *, status: RunStatus, error: str | None = None) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        tenant_id=_TENANT,
        thread_id=uuid4(),
        user_id=None,
        status=status,
        on_disconnect=DisconnectMode.CANCEL,
        is_resume=False,
        error=error,
        created_at=_BASE,
        updated_at=_BASE,
        finished_at=_BASE,
    )


def _fired_run(*, trigger_id: UUID, run_id: UUID, attempt: int = 1) -> TriggerRunRecord:
    return TriggerRunRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        trigger_id=trigger_id,
        run_id=run_id,
        status=TriggerRunStatus.FIRED,
        attempt=attempt,
        triggered_at=_BASE,
    )


# --- cron math ------------------------------------------------------------


def test_next_fire_computes_following_slot() -> None:
    after = datetime(2026, 5, 22, 8, 0, 0, tzinfo=UTC)
    assert _next_fire("0 9 * * *", after) == datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)


def test_is_cron_due_true_when_slot_passed() -> None:
    trig = _trigger(expr="0 9 * * *", created_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC))
    assert _is_cron_due(trig, now=datetime(2026, 5, 22, 10, 0, tzinfo=UTC)) is True


def test_is_cron_due_false_before_slot() -> None:
    trig = _trigger(expr="0 9 * * *", created_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC))
    assert _is_cron_due(trig, now=datetime(2026, 5, 22, 8, 30, tzinfo=UTC)) is False


def test_is_cron_due_false_right_after_last_fire() -> None:
    """A daily trigger that just fired is not due again until tomorrow."""
    fired = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)
    trig = _trigger(expr="0 9 * * *", last_fired_at=fired)
    assert _is_cron_due(trig, now=fired + timedelta(minutes=30)) is False


# --- run_once -------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_fires_due_trigger() -> None:
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    # created_at far in the past + a daily slot → due now.
    trig = await triggers.create(
        _trigger(expr="0 9 * * *", created_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    )
    scheduler, runtime = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs
    )

    fired = await scheduler.run_once()
    assert fired == 1

    runs = await trigger_runs.list_by_trigger(trigger_id=trig.id, tenant_id=_TENANT)
    assert len(runs) == 1
    assert runs[0].status is TriggerRunStatus.FIRED
    assert runs[0].run_id is not None

    refreshed = await triggers.get(trigger_id=trig.id, tenant_id=_TENANT)
    assert refreshed is not None
    assert refreshed.last_fired_at is not None  # stamped by the fire

    # Drain the spawned run worker so the loop has no dangling task.
    record = runtime.run_manager.get(runs[0].run_id)
    assert record is not None and record.task is not None
    await record.task


@pytest.mark.asyncio
async def test_run_once_skips_not_due_trigger() -> None:
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    # Fired moments ago — a daily trigger is not due again.
    trig = await triggers.create(_trigger(expr="0 9 * * *", last_fired_at=datetime.now(UTC)))
    scheduler, _ = await _build_scheduler(trigger_store=triggers, trigger_run_store=trigger_runs)

    fired = await scheduler.run_once()
    assert fired == 0
    runs = await trigger_runs.list_by_trigger(trigger_id=trig.id, tenant_id=_TENANT)
    assert runs == []


@pytest.mark.asyncio
async def test_run_once_skips_when_agent_missing() -> None:
    """A due trigger whose agent is gone fires nothing — and does not crash."""
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    await triggers.create(
        _trigger(expr="0 9 * * *", created_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    )
    scheduler, _ = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, seed_agent=False
    )

    fired = await scheduler.run_once()
    assert fired == 0


@pytest.mark.asyncio
async def test_run_once_survives_malformed_cron() -> None:
    """A bad cron expr fails its own trigger, not the whole sweep."""
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    await triggers.create(_trigger(name="bad", expr="not-a-cron"))
    scheduler, _ = await _build_scheduler(trigger_store=triggers, trigger_run_store=trigger_runs)

    fired = await scheduler.run_once()  # must not raise
    assert fired == 0


@pytest.mark.asyncio
async def test_start_stop_is_idempotent() -> None:
    scheduler, _ = await _build_scheduler(
        trigger_store=InMemoryTriggerStore(),
        trigger_run_store=InMemoryTriggerRunStore(),
    )
    assert scheduler.is_running is False
    scheduler.start()
    scheduler.start()  # idempotent
    assert scheduler.is_running is True
    await scheduler.stop()
    assert scheduler.is_running is False


# --- DLQ: reconcile + retry -----------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_marks_succeeded() -> None:
    trigger_runs = InMemoryTriggerRunStore()
    run_store = InMemoryRunStore()
    run_id, trigger_id = uuid4(), uuid4()
    await run_store.create(_run_info(run_id, status=RunStatus.SUCCESS))
    fired = await trigger_runs.create(_fired_run(trigger_id=trigger_id, run_id=run_id))
    scheduler, _ = await _build_scheduler(
        trigger_store=InMemoryTriggerStore(),
        trigger_run_store=trigger_runs,
        run_store=run_store,
    )

    await scheduler._reconcile_fired()

    row = await trigger_runs.get(trigger_run_id=fired.id, tenant_id=_TENANT)
    assert row is not None
    assert row.status is TriggerRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_reconcile_failure_schedules_retry() -> None:
    trigger_runs = InMemoryTriggerRunStore()
    run_store = InMemoryRunStore()
    run_id, trigger_id = uuid4(), uuid4()
    await run_store.create(_run_info(run_id, status=RunStatus.ERROR, error="boom"))
    fired = await trigger_runs.create(_fired_run(trigger_id=trigger_id, run_id=run_id, attempt=1))
    scheduler, _ = await _build_scheduler(
        trigger_store=InMemoryTriggerStore(),
        trigger_run_store=trigger_runs,
        run_store=run_store,
    )

    await scheduler._reconcile_fired()

    row = await trigger_runs.get(trigger_run_id=fired.id, tenant_id=_TENANT)
    assert row is not None
    assert row.status is TriggerRunStatus.RETRYING
    assert row.next_retry_at is not None
    assert row.error == "boom"


@pytest.mark.asyncio
async def test_reconcile_exhausted_budget_dead_letters() -> None:
    trigger_runs = InMemoryTriggerRunStore()
    run_store = InMemoryRunStore()
    run_id, trigger_id = uuid4(), uuid4()
    await run_store.create(_run_info(run_id, status=RunStatus.ERROR))
    fired = await trigger_runs.create(_fired_run(trigger_id=trigger_id, run_id=run_id, attempt=5))
    scheduler, _ = await _build_scheduler(
        trigger_store=InMemoryTriggerStore(),
        trigger_run_store=trigger_runs,
        run_store=run_store,
    )

    await scheduler._reconcile_fired()

    row = await trigger_runs.get(trigger_run_id=fired.id, tenant_id=_TENANT)
    assert row is not None
    assert row.status is TriggerRunStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_retry_re_fires_due_row() -> None:
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    # last_fired_at=now → pass-1 cron-fire won't also pick this up.
    trig = await triggers.create(_trigger(last_fired_at=datetime.now(UTC)))
    retrying = TriggerRunRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        trigger_id=trig.id,
        run_id=uuid4(),
        status=TriggerRunStatus.RETRYING,
        attempt=1,
        next_retry_at=datetime.now(UTC) - timedelta(minutes=1),
        triggered_at=_BASE,
    )
    await trigger_runs.create(retrying)
    scheduler, runtime = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs
    )

    fired = await scheduler._retry_due(datetime.now(UTC))
    assert fired == 1

    row = await trigger_runs.get(trigger_run_id=retrying.id, tenant_id=_TENANT)
    assert row is not None
    assert row.status is TriggerRunStatus.FIRED
    assert row.attempt == 2

    record = runtime.run_manager.get(row.run_id)
    assert record is not None and record.task is not None
    await record.task


@pytest.mark.asyncio
async def test_retry_skips_not_due_row() -> None:
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    trig = await triggers.create(_trigger(last_fired_at=datetime.now(UTC)))
    retrying = TriggerRunRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        trigger_id=trig.id,
        run_id=uuid4(),
        status=TriggerRunStatus.RETRYING,
        attempt=1,
        next_retry_at=datetime.now(UTC) + timedelta(hours=1),
        triggered_at=_BASE,
    )
    await trigger_runs.create(retrying)
    scheduler, _ = await _build_scheduler(trigger_store=triggers, trigger_run_store=trigger_runs)

    fired = await scheduler._retry_due(datetime.now(UTC))
    assert fired == 0


# --- Stream 9.5 — two-instance exactly-once (CAS guards) ------------------


async def _drain(run_id: UUID, *runtimes: AgentRuntime) -> None:
    """Await the spawned run worker wherever it landed (winner's runtime)."""
    for rt in runtimes:
        record = rt.run_manager.get(run_id)
        if record is not None and record.task is not None:
            await record.task
            return


@pytest.mark.asyncio
async def test_two_instances_fire_due_cron_exactly_once() -> None:
    """Blue + green both scan the same due cron trigger; the claim_cron_fire CAS
    lets exactly one fire — no duplicate run / trigger_run."""
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    runs = InMemoryRunStore()
    trig = await triggers.create(
        _trigger(expr="0 9 * * *", created_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    )
    blue, blue_rt = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=runs
    )
    green, green_rt = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=runs
    )

    counts = await asyncio.gather(blue.run_once(), green.run_once())
    assert sum(counts) == 1  # exactly one instance fired

    fired_rows = await trigger_runs.list_by_trigger(trigger_id=trig.id, tenant_id=_TENANT)
    assert len(fired_rows) == 1  # exactly one trigger_run — no double-fire
    await _drain(fired_rows[0].run_id, blue_rt, green_rt)


@pytest.mark.asyncio
async def test_two_instances_retry_exactly_once() -> None:
    """Two instances both sweep the same due retrying firing; the claim_retry CAS
    lets exactly one re-fire — attempt advances by one, not two."""
    triggers = InMemoryTriggerStore()
    trigger_runs = InMemoryTriggerRunStore()
    runs = InMemoryRunStore()
    trig = await triggers.create(_trigger(last_fired_at=datetime.now(UTC)))
    retrying = TriggerRunRecord(
        id=uuid4(),
        tenant_id=_TENANT,
        trigger_id=trig.id,
        run_id=uuid4(),
        status=TriggerRunStatus.RETRYING,
        attempt=1,
        next_retry_at=datetime.now(UTC) - timedelta(minutes=1),
        triggered_at=_BASE,
    )
    await trigger_runs.create(retrying)
    blue, blue_rt = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=runs
    )
    green, green_rt = await _build_scheduler(
        trigger_store=triggers, trigger_run_store=trigger_runs, run_store=runs
    )

    now = datetime.now(UTC)
    counts = await asyncio.gather(blue._retry_due(now), green._retry_due(now))
    assert sum(counts) == 1  # exactly one instance re-fired

    row = await trigger_runs.get(trigger_run_id=retrying.id, tenant_id=_TENANT)
    assert row is not None
    assert row.status is TriggerRunStatus.FIRED
    assert row.attempt == 2  # advanced once, not twice
    await _drain(row.run_id, blue_rt, green_rt)
