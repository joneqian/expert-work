"""Unit tests for :class:`InMemoryEvalRunStore` — P1-S2.1.

Pins the store contract the resident ``EvalWorker`` relies on: status
machine timestamps, tenant isolation, cross-tenant queued scan, and
case-result append/list. The Postgres backend is covered separately
against a real PG (integration).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryEvalRunStore
from helix_agent.protocol import (
    EvalCaseResultRecord,
    EvalRunRecord,
    EvalRunStatus,
    EvalTriggeredBy,
)


def _run(tenant_id: object, *, status: EvalRunStatus = EvalRunStatus.QUEUED) -> EvalRunRecord:
    return EvalRunRecord(
        id=uuid4(),
        tenant_id=tenant_id,  # type: ignore[arg-type]
        suite="m0_baseline",
        status=status,
        triggered_by=EvalTriggeredBy.MANUAL,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_and_get_is_tenant_scoped() -> None:
    store = InMemoryEvalRunStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    run = await store.create_run(_run(tenant_a))

    assert await store.get_run(run_id=run.id, tenant_id=tenant_a) == run
    # Cross-tenant read must miss.
    assert await store.get_run(run_id=run.id, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_set_status_stamps_timestamps_and_summary() -> None:
    store = InMemoryEvalRunStore()
    tenant = uuid4()
    run = await store.create_run(_run(tenant))
    assert run.started_at is None and run.finished_at is None

    assert await store.set_status(run_id=run.id, tenant_id=tenant, status=EvalRunStatus.RUNNING)
    running = await store.get_run(run_id=run.id, tenant_id=tenant)
    assert running is not None
    assert running.status is EvalRunStatus.RUNNING
    assert running.started_at is not None and running.finished_at is None

    summary: dict[str, object] = {"pass_count": 15, "total": 15}
    assert await store.set_status(
        run_id=run.id, tenant_id=tenant, status=EvalRunStatus.PASSED, summary=summary
    )
    done = await store.get_run(run_id=run.id, tenant_id=tenant)
    assert done is not None
    assert done.status is EvalRunStatus.PASSED
    assert done.finished_at is not None
    assert done.summary == summary


@pytest.mark.asyncio
async def test_set_status_cross_tenant_is_noop() -> None:
    store = InMemoryEvalRunStore()
    tenant, other = uuid4(), uuid4()
    run = await store.create_run(_run(tenant))
    assert (
        await store.set_status(run_id=run.id, tenant_id=other, status=EvalRunStatus.RUNNING)
        is False
    )


@pytest.mark.asyncio
async def test_list_by_status_all_tenants_spans_tenants_oldest_first() -> None:
    store = InMemoryEvalRunStore()
    a, b = uuid4(), uuid4()
    r1 = await store.create_run(_run(a))
    r2 = await store.create_run(_run(b))
    # One run advances out of queued — must drop from the queued scan.
    await store.set_status(run_id=r2.id, tenant_id=b, status=EvalRunStatus.RUNNING)

    queued = await store.list_by_status_all_tenants(EvalRunStatus.QUEUED)
    assert [r.id for r in queued] == [r1.id]
    running = await store.list_by_status_all_tenants(EvalRunStatus.RUNNING)
    assert [r.id for r in running] == [r2.id]


@pytest.mark.asyncio
async def test_append_and_list_case_results() -> None:
    store = InMemoryEvalRunStore()
    tenant = uuid4()
    run = await store.create_run(_run(tenant))

    c1 = await store.append_case_result(
        EvalCaseResultRecord(
            run_id=run.id,
            tenant_id=tenant,
            capability="J.1_plan_execute",
            case_id="case-1",
            passed=True,
            scores={"pass_rate": 1.0},
        )
    )
    c2 = await store.append_case_result(
        EvalCaseResultRecord(
            run_id=run.id,
            tenant_id=tenant,
            capability="J.2_reflect",
            case_id="case-2",
            passed=False,
        )
    )
    # Bigserial ids are assigned on append.
    assert c1.id == 1 and c2.id == 2

    results = await store.list_case_results(run_id=run.id, tenant_id=tenant)
    assert [r.case_id for r in results] == ["case-1", "case-2"]
    # Cross-tenant read of the same run id returns nothing.
    assert await store.list_case_results(run_id=run.id, tenant_id=uuid4()) == []
