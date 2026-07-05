"""Unit tests for ``RunStore.list_running_for_agent`` — Stream RT-4 (RT-ADR-17).

The agent-level kill switch bulk-cancels an agent's in-flight runs; this method
is the enumeration step. Runs carry no ``agent_name`` — the binding lives on
``thread_meta`` — so the in-memory double resolves via an injected thread store
(the SQL backend joins). Covered here against the in-memory backend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence.thread_meta import InMemoryThreadMetaStore
from helix_agent.runtime.runs import DisconnectMode, InMemoryRunStore, RunInfo, RunStatus

_BASE = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)


def _info(
    *,
    run_id: UUID,
    tenant_id: UUID,
    thread_id: UUID,
    status: RunStatus,
) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        user_id=None,
        status=status,
        on_disconnect=DisconnectMode.CANCEL,
        is_resume=False,
        error=None,
        created_at=_BASE,
        updated_at=_BASE,
        finished_at=None,
    )


async def _seed_thread(
    threads: InMemoryThreadMetaStore, *, thread_id: UUID, tenant_id: UUID, agent_name: str
) -> None:
    await threads.create(
        thread_id=thread_id,
        tenant_id=tenant_id,
        created_by="seed",
        agent_name=agent_name,
        agent_version="1.0.0",
    )


@pytest.mark.asyncio
async def test_returns_running_runs_for_agent_only() -> None:
    threads = InMemoryThreadMetaStore()
    store = InMemoryRunStore(thread_meta_store=threads)
    tenant = uuid4()

    t_a1, t_a2, t_b = uuid4(), uuid4(), uuid4()
    await _seed_thread(threads, thread_id=t_a1, tenant_id=tenant, agent_name="a")
    await _seed_thread(threads, thread_id=t_a2, tenant_id=tenant, agent_name="a")
    await _seed_thread(threads, thread_id=t_b, tenant_id=tenant, agent_name="b")

    run_a_running = uuid4()
    run_a_done = uuid4()
    run_b_running = uuid4()
    await store.create(
        _info(run_id=run_a_running, tenant_id=tenant, thread_id=t_a1, status=RunStatus.RUNNING)
    )
    await store.create(
        _info(run_id=run_a_done, tenant_id=tenant, thread_id=t_a2, status=RunStatus.SUCCESS)
    )
    await store.create(
        _info(run_id=run_b_running, tenant_id=tenant, thread_id=t_b, status=RunStatus.RUNNING)
    )

    running = await store.list_running_for_agent(tenant_id=tenant, agent_name="a")
    ids = {r.run_id for r in running}
    assert ids == {run_a_running}  # not the SUCCESS run, not agent b's run


@pytest.mark.asyncio
async def test_scoped_by_tenant() -> None:
    threads = InMemoryThreadMetaStore()
    store = InMemoryRunStore(thread_meta_store=threads)
    tenant_a, tenant_b = uuid4(), uuid4()

    t_a, t_b = uuid4(), uuid4()
    await _seed_thread(threads, thread_id=t_a, tenant_id=tenant_a, agent_name="shared")
    await _seed_thread(threads, thread_id=t_b, tenant_id=tenant_b, agent_name="shared")
    run_a, run_b = uuid4(), uuid4()
    await store.create(
        _info(run_id=run_a, tenant_id=tenant_a, thread_id=t_a, status=RunStatus.RUNNING)
    )
    await store.create(
        _info(run_id=run_b, tenant_id=tenant_b, thread_id=t_b, status=RunStatus.RUNNING)
    )

    running = await store.list_running_for_agent(tenant_id=tenant_a, agent_name="shared")
    assert {r.run_id for r in running} == {run_a}


@pytest.mark.asyncio
async def test_no_thread_store_returns_empty() -> None:
    # Without a thread store the in-memory double cannot resolve run → agent.
    store = InMemoryRunStore()
    tenant = uuid4()
    await store.create(
        _info(run_id=uuid4(), tenant_id=tenant, thread_id=uuid4(), status=RunStatus.RUNNING)
    )
    assert await store.list_running_for_agent(tenant_id=tenant, agent_name="a") == []
