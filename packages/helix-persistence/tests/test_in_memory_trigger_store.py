"""Unit tests for InMemoryTriggerStore — Stream J.10 (Mini-ADR J-26 / J-42)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence import InMemoryTriggerRunStore, InMemoryTriggerStore
from helix_agent.protocol import (
    TriggerKind,
    TriggerRecord,
    TriggerRunRecord,
    TriggerRunStatus,
)

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _record(
    *,
    trigger_id: UUID | None = None,
    tenant_id: UUID | None = None,
    agent_name: str = "reporter",
    name: str = "nightly",
    kind: TriggerKind = "cron",
    enabled: bool = True,
) -> TriggerRecord:
    config: dict[str, object] = {"expr": "0 9 * * *"} if kind == "cron" else {}
    return TriggerRecord(
        id=trigger_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        agent_name=agent_name,
        agent_version="1.0.0",
        name=name,
        kind=kind,
        config=config,
        enabled=enabled,
        source="api",
        created_at=_BASE,
        updated_at=_BASE,
    )


@pytest.mark.asyncio
async def test_create_then_get_round_trips() -> None:
    store = InMemoryTriggerStore()
    tid, tenant = uuid4(), uuid4()
    await store.create(_record(trigger_id=tid, tenant_id=tenant))

    fetched = await store.get(trigger_id=tid, tenant_id=tenant)
    assert fetched is not None
    assert fetched.id == tid
    assert fetched.kind == "cron"


@pytest.mark.asyncio
async def test_get_unknown_returns_none() -> None:
    store = InMemoryTriggerStore()
    assert await store.get(trigger_id=uuid4(), tenant_id=uuid4()) is None


@pytest.mark.asyncio
async def test_get_cross_tenant_returns_none() -> None:
    store = InMemoryTriggerStore()
    tid, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_record(trigger_id=tid, tenant_id=tenant_a))

    assert await store.get(trigger_id=tid, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_create_duplicate_name_per_agent_raises() -> None:
    store = InMemoryTriggerStore()
    tenant = uuid4()
    await store.create(_record(tenant_id=tenant, agent_name="reporter", name="nightly"))
    with pytest.raises(ValueError, match="already exists"):
        await store.create(_record(tenant_id=tenant, agent_name="reporter", name="nightly"))


@pytest.mark.asyncio
async def test_same_name_different_agent_is_allowed() -> None:
    """The uniqueness key is (tenant, agent, name) — not (tenant, name)."""
    store = InMemoryTriggerStore()
    tenant = uuid4()
    await store.create(_record(tenant_id=tenant, agent_name="reporter", name="nightly"))
    await store.create(_record(tenant_id=tenant, agent_name="auditor", name="nightly"))


@pytest.mark.asyncio
async def test_list_by_agent_filters() -> None:
    store = InMemoryTriggerStore()
    tenant = uuid4()
    await store.create(_record(tenant_id=tenant, agent_name="reporter", name="a"))
    await store.create(_record(tenant_id=tenant, agent_name="reporter", name="b"))
    await store.create(_record(tenant_id=tenant, agent_name="auditor", name="a"))

    listed = await store.list_by_agent(tenant_id=tenant, agent_name="reporter")
    assert {r.name for r in listed} == {"a", "b"}


@pytest.mark.asyncio
async def test_list_enabled_cron_is_cross_tenant_and_kind_filtered() -> None:
    store = InMemoryTriggerStore()
    await store.create(_record(tenant_id=uuid4(), name="c1", kind="cron", enabled=True))
    await store.create(_record(tenant_id=uuid4(), name="c2", kind="cron", enabled=True))
    await store.create(_record(tenant_id=uuid4(), name="c3", kind="cron", enabled=False))
    await store.create(_record(tenant_id=uuid4(), name="w1", kind="webhook"))

    listed = await store.list_enabled_cron()
    assert {r.name for r in listed} == {"c1", "c2"}


@pytest.mark.asyncio
async def test_update_replaces_row() -> None:
    store = InMemoryTriggerStore()
    tid, tenant = uuid4(), uuid4()
    await store.create(_record(trigger_id=tid, tenant_id=tenant, enabled=True))

    rec = await store.get(trigger_id=tid, tenant_id=tenant)
    assert rec is not None
    updated = await store.update(rec.model_copy(update={"enabled": False}))
    assert updated is True

    again = await store.get(trigger_id=tid, tenant_id=tenant)
    assert again is not None
    assert again.enabled is False


@pytest.mark.asyncio
async def test_update_cross_tenant_returns_false() -> None:
    store = InMemoryTriggerStore()
    tid, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_record(trigger_id=tid, tenant_id=tenant_a))

    rec = await store.get(trigger_id=tid, tenant_id=tenant_a)
    assert rec is not None
    impostor = rec.model_copy(update={"tenant_id": tenant_b})
    updated = await store.update(impostor)
    assert updated is False


@pytest.mark.asyncio
async def test_delete() -> None:
    store = InMemoryTriggerStore()
    tid, tenant = uuid4(), uuid4()
    await store.create(_record(trigger_id=tid, tenant_id=tenant))

    deleted = await store.delete(trigger_id=tid, tenant_id=tenant)
    assert deleted is True
    assert await store.get(trigger_id=tid, tenant_id=tenant) is None
    deleted_again = await store.delete(trigger_id=tid, tenant_id=tenant)
    assert deleted_again is False


@pytest.mark.asyncio
async def test_get_for_webhook_is_tenant_unscoped() -> None:
    """The webhook ingest path resolves a trigger by id alone."""
    store = InMemoryTriggerStore()
    tid, tenant = uuid4(), uuid4()
    await store.create(_record(trigger_id=tid, tenant_id=tenant, kind="webhook"))

    found = await store.get_for_webhook(trigger_id=tid)
    assert found is not None
    assert found.tenant_id == tenant
    assert await store.get_for_webhook(trigger_id=uuid4()) is None


# --- InMemoryTriggerRunStore ----------------------------------------------


def _run_record(
    *,
    run_record_id: UUID | None = None,
    tenant_id: UUID | None = None,
    trigger_id: UUID | None = None,
    status: TriggerRunStatus = TriggerRunStatus.FIRED,
    triggered_at: datetime = _BASE,
    next_retry_at: datetime | None = None,
) -> TriggerRunRecord:
    return TriggerRunRecord(
        id=run_record_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        trigger_id=trigger_id or uuid4(),
        run_id=uuid4(),
        status=status,
        attempt=1,
        next_retry_at=next_retry_at,
        triggered_at=triggered_at,
    )


@pytest.mark.asyncio
async def test_run_store_create_then_get() -> None:
    store = InMemoryTriggerRunStore()
    rid, tenant = uuid4(), uuid4()
    await store.create(_run_record(run_record_id=rid, tenant_id=tenant))

    fetched = await store.get(trigger_run_id=rid, tenant_id=tenant)
    assert fetched is not None
    assert fetched.id == rid
    assert fetched.status is TriggerRunStatus.FIRED


@pytest.mark.asyncio
async def test_run_store_get_unknown_and_cross_tenant() -> None:
    store = InMemoryTriggerRunStore()
    rid, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_run_record(run_record_id=rid, tenant_id=tenant_a))

    assert await store.get(trigger_run_id=uuid4(), tenant_id=tenant_a) is None
    assert await store.get(trigger_run_id=rid, tenant_id=tenant_b) is None


@pytest.mark.asyncio
async def test_run_store_create_duplicate_id_raises() -> None:
    store = InMemoryTriggerRunStore()
    rid = uuid4()
    await store.create(_run_record(run_record_id=rid))
    with pytest.raises(ValueError, match="already exists"):
        await store.create(_run_record(run_record_id=rid))


@pytest.mark.asyncio
async def test_run_store_update_transitions_status() -> None:
    store = InMemoryTriggerRunStore()
    rid, tenant = uuid4(), uuid4()
    await store.create(_run_record(run_record_id=rid, tenant_id=tenant))

    rec = await store.get(trigger_run_id=rid, tenant_id=tenant)
    assert rec is not None
    done = await store.update(rec.model_copy(update={"status": TriggerRunStatus.SUCCEEDED}))
    assert done is True

    again = await store.get(trigger_run_id=rid, tenant_id=tenant)
    assert again is not None
    assert again.status is TriggerRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_run_store_update_cross_tenant_returns_false() -> None:
    store = InMemoryTriggerRunStore()
    rid, tenant_a, tenant_b = uuid4(), uuid4(), uuid4()
    await store.create(_run_record(run_record_id=rid, tenant_id=tenant_a))

    rec = await store.get(trigger_run_id=rid, tenant_id=tenant_a)
    assert rec is not None
    miss = await store.update(rec.model_copy(update={"tenant_id": tenant_b}))
    assert miss is False


@pytest.mark.asyncio
async def test_run_store_list_by_trigger_filters_and_sorts() -> None:
    store = InMemoryTriggerRunStore()
    tenant, trigger_id = uuid4(), uuid4()
    await store.create(_run_record(tenant_id=tenant, trigger_id=trigger_id, triggered_at=_BASE))
    await store.create(
        _run_record(
            tenant_id=tenant,
            trigger_id=trigger_id,
            triggered_at=_BASE.replace(hour=13),
        )
    )
    await store.create(_run_record(tenant_id=tenant, trigger_id=uuid4()))

    listed = await store.list_by_trigger(trigger_id=trigger_id, tenant_id=tenant)
    assert len(listed) == 2
    # newest first
    assert listed[0].triggered_at > listed[1].triggered_at


@pytest.mark.asyncio
async def test_count_cron_by_tenant() -> None:
    store = InMemoryTriggerStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    await store.create(_record(tenant_id=tenant_a, name="c1", kind="cron"))
    await store.create(_record(tenant_id=tenant_a, name="c2", kind="cron"))
    await store.create(_record(tenant_id=tenant_a, name="w1", kind="webhook"))
    await store.create(_record(tenant_id=tenant_b, name="c1", kind="cron"))

    assert await store.count_cron_by_tenant(tenant_id=tenant_a) == 2
    assert await store.count_cron_by_tenant(tenant_id=tenant_b) == 1
    assert await store.count_cron_by_tenant(tenant_id=uuid4()) == 0


@pytest.mark.asyncio
async def test_run_store_list_fired_is_status_filtered() -> None:
    store = InMemoryTriggerRunStore()
    await store.create(_run_record(status=TriggerRunStatus.FIRED))
    await store.create(_run_record(status=TriggerRunStatus.FIRED))
    await store.create(_run_record(status=TriggerRunStatus.SUCCEEDED))
    await store.create(_run_record(status=TriggerRunStatus.RETRYING))

    listed = await store.list_fired()
    assert len(listed) == 2
    assert all(r.status is TriggerRunStatus.FIRED for r in listed)


@pytest.mark.asyncio
async def test_run_store_list_due_retries() -> None:
    store = InMemoryTriggerRunStore()
    now = datetime(2026, 5, 22, 15, 0, 0, tzinfo=UTC)
    await store.create(
        _run_record(status=TriggerRunStatus.RETRYING, next_retry_at=now - timedelta(minutes=1))
    )
    await store.create(
        _run_record(status=TriggerRunStatus.RETRYING, next_retry_at=now + timedelta(hours=1))
    )
    await store.create(_run_record(status=TriggerRunStatus.FIRED))

    due = await store.list_due_retries(before=now)
    assert len(due) == 1
    assert due[0].status is TriggerRunStatus.RETRYING
