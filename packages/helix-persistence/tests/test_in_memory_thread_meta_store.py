"""Unit tests for InMemoryThreadMetaStore — Repository contract."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryThreadMetaStore
from helix_agent.protocol import ThreadStatus


@pytest.mark.asyncio
async def test_create_and_get_round_trip() -> None:
    store = InMemoryThreadMetaStore()
    thread_id, tenant_id = uuid4(), uuid4()

    created = await store.create(
        thread_id=thread_id,
        tenant_id=tenant_id,
        created_by="user-1",
        agent_name="demo",
        agent_version="0.1.0",
    )
    assert created.thread_id == thread_id
    assert created.status == ThreadStatus.ACTIVE
    assert created.agent_version == "0.1.0"

    fetched = await store.get(thread_id, tenant_id=tenant_id)
    assert fetched is not None
    assert fetched.created_by == "user-1"


@pytest.mark.asyncio
async def test_get_filters_by_tenant() -> None:
    store = InMemoryThreadMetaStore()
    thread_id, owner, other = uuid4(), uuid4(), uuid4()
    await store.create(thread_id=thread_id, tenant_id=owner, created_by="x")

    assert await store.get(thread_id, tenant_id=other) is None
    assert await store.get(thread_id, tenant_id=owner) is not None


@pytest.mark.asyncio
async def test_user_id_round_trip_and_list_filter() -> None:
    store = InMemoryThreadMetaStore()
    tenant_id, user_a, user_b = uuid4(), uuid4(), uuid4()

    t_a = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x", user_id=user_a)
    assert t_a.user_id == user_a
    await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x", user_id=user_b)
    # A thread with no owner (machine-triggered) keeps user_id None.
    unowned = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")
    assert unowned.user_id is None

    only_a = await store.list_by_tenant(tenant_id, user_id=user_a)
    assert [m.user_id for m in only_a] == [user_a]
    # No user filter → all three threads.
    assert len(await store.list_by_tenant(tenant_id)) == 3


@pytest.mark.asyncio
async def test_create_rejects_duplicate_thread_id() -> None:
    store = InMemoryThreadMetaStore()
    thread_id, tenant_id = uuid4(), uuid4()
    await store.create(thread_id=thread_id, tenant_id=tenant_id, created_by="x")
    with pytest.raises(ValueError, match="already exists"):
        await store.create(thread_id=thread_id, tenant_id=tenant_id, created_by="x")


@pytest.mark.asyncio
async def test_list_by_tenant_pagination_and_status_filter() -> None:
    store = InMemoryThreadMetaStore()
    tenant_id = uuid4()
    threads = [uuid4() for _ in range(5)]
    for t in threads:
        await store.create(thread_id=t, tenant_id=tenant_id, created_by="x")

    await store.update_status(threads[0], ThreadStatus.COMPLETED, tenant_id=tenant_id)

    all_active = await store.list_by_tenant(tenant_id, status=ThreadStatus.ACTIVE)
    assert len(all_active) == 4

    page = await store.list_by_tenant(tenant_id, limit=2, offset=0)
    assert len(page) == 2


@pytest.mark.asyncio
async def test_update_status_returns_true_only_on_match() -> None:
    store = InMemoryThreadMetaStore()
    thread_id, owner, other = uuid4(), uuid4(), uuid4()
    await store.create(thread_id=thread_id, tenant_id=owner, created_by="x")

    # Bind awaited mutations before asserting; `assert await foo()` is stripped
    # under `python -O` and the side effect would disappear silently.
    owner_match = await store.update_status(thread_id, ThreadStatus.PAUSED, tenant_id=owner)
    assert owner_match is True
    other_mismatch = await store.update_status(thread_id, ThreadStatus.PAUSED, tenant_id=other)
    assert other_mismatch is False
    missing = await store.update_status(uuid4(), ThreadStatus.PAUSED, tenant_id=owner)
    assert missing is False


@pytest.mark.asyncio
async def test_check_access_and_delete() -> None:
    store = InMemoryThreadMetaStore()
    thread_id, owner, other = uuid4(), uuid4(), uuid4()
    await store.create(thread_id=thread_id, tenant_id=owner, created_by="x")

    owner_access = await store.check_access(thread_id, owner)
    assert owner_access is True
    other_access = await store.check_access(thread_id, other)
    assert other_access is False

    wrong_tenant_delete = await store.delete(thread_id, tenant_id=other)
    assert wrong_tenant_delete is False
    still_accessible = await store.check_access(thread_id, owner)
    assert still_accessible is True

    owner_delete = await store.delete(thread_id, tenant_id=owner)
    assert owner_delete is True
    gone = await store.check_access(thread_id, owner)
    assert gone is False


# ---------------------------------------------------------------------------
# Stream H.6 (Mini-ADR H-10) — agent_name / agent_version list filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_tenant_agent_filters() -> None:
    store = InMemoryThreadMetaStore()
    tenant_id = uuid4()
    await store.create(
        thread_id=uuid4(),
        tenant_id=tenant_id,
        created_by="x",
        agent_name="reporter",
        agent_version="1.0.0",
    )
    await store.create(
        thread_id=uuid4(),
        tenant_id=tenant_id,
        created_by="x",
        agent_name="reporter",
        agent_version="2.0.0",
    )
    await store.create(
        thread_id=uuid4(),
        tenant_id=tenant_id,
        created_by="x",
        agent_name="scribe",
        agent_version="1.0.0",
    )
    # Machine thread with no agent binding stays out of any agent filter.
    await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")

    by_name = await store.list_by_tenant(tenant_id, agent_name="reporter")
    assert {m.agent_version for m in by_name} == {"1.0.0", "2.0.0"}

    by_name_version = await store.list_by_tenant(
        tenant_id, agent_name="reporter", agent_version="2.0.0"
    )
    assert [m.agent_version for m in by_name_version] == ["2.0.0"]

    assert await store.list_by_tenant(tenant_id, agent_name="ghost") == []
    # No filter → all four threads (regression).
    assert len(await store.list_by_tenant(tenant_id)) == 4


@pytest.mark.asyncio
async def test_list_all_tenants_agent_filters() -> None:
    store = InMemoryThreadMetaStore()
    await store.create(thread_id=uuid4(), tenant_id=uuid4(), created_by="x", agent_name="reporter")
    await store.create(thread_id=uuid4(), tenant_id=uuid4(), created_by="x", agent_name="scribe")

    cross = await store.list_all_tenants(agent_name="reporter")
    assert [m.agent_name for m in cross] == ["reporter"]
    assert len(await store.list_all_tenants()) == 2


# ---------------------------------------------------------------------------
# Session-history uplift — title (auto/rename), search, archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_title_round_trip_and_tenant_scope() -> None:
    store = InMemoryThreadMetaStore()
    thread_id, owner, other = uuid4(), uuid4(), uuid4()
    created = await store.create(thread_id=thread_id, tenant_id=owner, created_by="x")
    assert created.title is None

    wrong = await store.update_title(thread_id, "hijack", tenant_id=other)
    assert wrong is False
    ok = await store.update_title(thread_id, "帮我写季度报告", tenant_id=owner)
    assert ok is True

    fetched = await store.get(thread_id, tenant_id=owner)
    assert fetched is not None
    assert fetched.title == "帮我写季度报告"

    missing = await store.update_title(uuid4(), "ghost", tenant_id=owner)
    assert missing is False


@pytest.mark.asyncio
async def test_list_q_matches_title_case_insensitive() -> None:
    store = InMemoryThreadMetaStore()
    tenant_id = uuid4()
    t_report = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")
    t_weather = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")
    await store.update_title(t_report.thread_id, "Quarterly Report", tenant_id=tenant_id)
    await store.update_title(t_weather.thread_id, "今天天气", tenant_id=tenant_id)
    # Untitled thread never matches a search.
    await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")

    hit = await store.list_by_tenant(tenant_id, q="report")
    assert [m.thread_id for m in hit] == [t_report.thread_id]
    assert await store.list_by_tenant(tenant_id, q="天气") == [
        await store.get(t_weather.thread_id, tenant_id=tenant_id)
    ]
    assert await store.list_by_tenant(tenant_id, q="nonexistent") == []


@pytest.mark.asyncio
async def test_archived_excluded_by_default_but_reachable() -> None:
    store = InMemoryThreadMetaStore()
    tenant_id = uuid4()
    keep = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")
    archived = await store.create(thread_id=uuid4(), tenant_id=tenant_id, created_by="x")
    await store.update_status(archived.thread_id, ThreadStatus.ARCHIVED, tenant_id=tenant_id)

    default = await store.list_by_tenant(tenant_id)
    assert [m.thread_id for m in default] == [keep.thread_id]

    with_archived = await store.list_by_tenant(tenant_id, include_archived=True)
    assert {m.thread_id for m in with_archived} == {keep.thread_id, archived.thread_id}

    only_archived = await store.list_by_tenant(tenant_id, status=ThreadStatus.ARCHIVED)
    assert [m.thread_id for m in only_archived] == [archived.thread_id]

    # Cross-tenant listing applies the same default exclusion.
    cross_default = await store.list_all_tenants()
    assert archived.thread_id not in {m.thread_id for m in cross_default}
    cross_all = await store.list_all_tenants(include_archived=True)
    assert archived.thread_id in {m.thread_id for m in cross_all}
