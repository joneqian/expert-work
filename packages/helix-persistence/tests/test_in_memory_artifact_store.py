"""Unit tests for InMemoryArtifactStore — Stream J.9 contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryArtifactStore


@pytest.mark.asyncio
async def test_save_version_creates_artifact_at_version_one() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()

    version = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="report.md",
        created_in_thread="t-1",
    )
    assert version.version == 1
    assert version.tenant_id == tenant_id
    assert version.size_bytes is None
    assert version.sha256 is None

    artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
    assert len(artifacts) == 1
    assert artifacts[0].name == "report.md"
    assert artifacts[0].kind == "document"
    assert artifacts[0].latest_version == 1


@pytest.mark.asyncio
async def test_save_version_appends_new_version_for_same_name() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()

    v1 = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="report.md",
        created_in_thread="t-1",
    )
    v2 = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="report.md",
        created_in_thread="t-2",
    )
    assert (v1.version, v2.version) == (1, 2)
    assert v1.artifact_id == v2.artifact_id

    artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
    assert len(artifacts) == 1
    assert artifacts[0].latest_version == 2


@pytest.mark.asyncio
async def test_save_version_keeps_original_kind() -> None:
    # A later save never changes an existing artifact's kind.
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()

    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="x",
        kind="document",
        path_in_workspace="x",
        created_in_thread="t-1",
    )
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="x",
        kind="code",
        path_in_workspace="x",
        created_in_thread="t-2",
    )
    artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
    assert artifacts[0].kind == "document"


@pytest.mark.asyncio
async def test_list_for_user_filters_by_tenant_and_user() -> None:
    store = InMemoryArtifactStore()
    tenant_a, tenant_b = uuid4(), uuid4()
    user_x, user_y = uuid4(), uuid4()

    await store.save_version(
        tenant_id=tenant_a,
        user_id=user_x,
        name="a",
        kind="data",
        path_in_workspace="a",
        created_in_thread="t",
    )
    # Same name, different user / tenant → separate artifacts.
    await store.save_version(
        tenant_id=tenant_a,
        user_id=user_y,
        name="a",
        kind="data",
        path_in_workspace="a",
        created_in_thread="t",
    )
    await store.save_version(
        tenant_id=tenant_b,
        user_id=user_x,
        name="a",
        kind="data",
        path_in_workspace="a",
        created_in_thread="t",
    )

    assert len(await store.list_for_user(tenant_id=tenant_a, user_id=user_x)) == 1
    assert len(await store.list_for_user(tenant_id=tenant_a, user_id=user_y)) == 1
    assert await store.list_for_user(tenant_id=uuid4(), user_id=user_x) == []


@pytest.mark.asyncio
async def test_get_latest_version_returns_newest_revision() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="v1.md",
        created_in_thread="t-1",
    )
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="v2.md",
        created_in_thread="t-2",
    )
    latest = await store.get_latest_version(tenant_id=tenant_id, user_id=user_id, name="report.md")
    assert latest is not None
    assert latest.version == 2
    assert latest.path_in_workspace == "v2.md"


@pytest.mark.asyncio
async def test_get_latest_version_unknown_name_returns_none() -> None:
    store = InMemoryArtifactStore()
    assert (
        await store.get_latest_version(tenant_id=uuid4(), user_id=uuid4(), name="missing") is None
    )


@pytest.mark.asyncio
async def test_soft_delete_hides_from_list_and_get() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="report.md",
        created_in_thread="t-1",
    )
    now = datetime.now(UTC)
    hit = await store.soft_delete(tenant_id=tenant_id, user_id=user_id, name="report.md", now=now)
    assert hit is True
    # Default list hides; include_deleted=True reveals.
    assert await store.list_for_user(tenant_id=tenant_id, user_id=user_id) == []
    deleted = await store.list_for_user(tenant_id=tenant_id, user_id=user_id, include_deleted=True)
    assert len(deleted) == 1
    assert deleted[0].deleted_at == now
    # get_latest_version hides soft-deleted (404-equivalent).
    assert (
        await store.get_latest_version(tenant_id=tenant_id, user_id=user_id, name="report.md")
        is None
    )


@pytest.mark.asyncio
async def test_soft_delete_idempotent_and_cross_user_safe() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_x, user_y = uuid4(), uuid4(), uuid4()
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_x,
        name="report.md",
        kind="document",
        path_in_workspace="report.md",
        created_in_thread="t-1",
    )
    now = datetime.now(UTC)
    # Cross-user soft-delete misses (same 404 hiding rule).
    miss_cross_user = await store.soft_delete(
        tenant_id=tenant_id, user_id=user_y, name="report.md", now=now
    )
    assert miss_cross_user is False
    # First soft-delete hits.
    assert await store.soft_delete(tenant_id=tenant_id, user_id=user_x, name="report.md", now=now)
    # Second soft-delete misses (idempotent).
    assert not await store.soft_delete(
        tenant_id=tenant_id, user_id=user_x, name="report.md", now=now
    )


@pytest.mark.asyncio
async def test_save_version_undeletes_soft_deleted_row() -> None:
    """Mini-ADR J-25 — a re-save on a soft-deleted name un-deletes it."""
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="v1.md",
        created_in_thread="t-1",
    )
    await store.soft_delete(
        tenant_id=tenant_id, user_id=user_id, name="report.md", now=datetime.now(UTC)
    )
    # Re-save un-deletes; latest_version bumps to 2.
    v2 = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="v2.md",
        created_in_thread="t-2",
    )
    assert v2.version == 2
    artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
    assert len(artifacts) == 1
    assert artifacts[0].deleted_at is None
    assert artifacts[0].latest_version == 2


@pytest.mark.asyncio
async def test_list_expired_returns_soft_deleted_past_horizon() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    # Two artifacts; soft-delete both at different times.
    for name in ("old.md", "new.md"):
        await store.save_version(
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            kind="document",
            path_in_workspace=name,
            created_in_thread="t",
        )
    old_time = datetime.now(UTC) - timedelta(days=70)
    recent_time = datetime.now(UTC) - timedelta(days=10)
    await store.soft_delete(tenant_id=tenant_id, user_id=user_id, name="old.md", now=old_time)
    await store.soft_delete(tenant_id=tenant_id, user_id=user_id, name="new.md", now=recent_time)
    # Cutoff 60 days ago → only ``old.md`` is past horizon.
    cutoff = datetime.now(UTC) - timedelta(days=60)
    expired = await store.list_expired(before=cutoff)
    assert [a.name for a in expired] == ["old.md"]


@pytest.mark.asyncio
async def test_list_active_past_retention_picks_stale_active_only() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    v = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="stale.md",
        kind="document",
        path_in_workspace="stale.md",
        created_in_thread="t-old",
    )
    # Hand-tune updated_at into the past.
    artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
    stale = artifacts[0]
    backdated = stale.model_copy(update={"updated_at": datetime.now(UTC) - timedelta(days=100)})
    store._artifacts[stale.id] = backdated  # type: ignore[attr-defined]
    # Also create a fresh active row.
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="fresh.md",
        kind="document",
        path_in_workspace="fresh.md",
        created_in_thread="t-new",
    )
    cutoff = datetime.now(UTC) - timedelta(days=90)
    rows = await store.list_active_past_retention(before=cutoff)
    assert [a.name for a in rows] == ["stale.md"]
    assert v.version == 1  # unchanged sanity


@pytest.mark.asyncio
async def test_hard_delete_removes_artifact_and_versions() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    v = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="v1.md",
        created_in_thread="t-1",
    )
    await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="report.md",
        kind="document",
        path_in_workspace="v2.md",
        created_in_thread="t-2",
    )
    artifacts = await store.list_for_user(tenant_id=tenant_id, user_id=user_id)
    assert len(artifacts) == 1
    artifact_id = artifacts[0].id
    removed = await store.hard_delete(artifact_ids=[artifact_id])
    assert removed == 1
    # Subsequent list returns empty; versions table also cleared.
    assert await store.list_for_user(tenant_id=tenant_id, user_id=user_id) == []
    assert (
        await store.list_for_user(tenant_id=tenant_id, user_id=user_id, include_deleted=True) == []
    )
    assert v.version == 1  # sanity


@pytest.mark.asyncio
async def test_hard_delete_empty_input_is_zero() -> None:
    store = InMemoryArtifactStore()
    assert await store.hard_delete(artifact_ids=[]) == 0


@pytest.mark.asyncio
async def test_set_version_digest_backfills_size_and_sha() -> None:
    store = InMemoryArtifactStore()
    tenant_id, user_id = uuid4(), uuid4()
    version = await store.save_version(
        tenant_id=tenant_id,
        user_id=user_id,
        name="data.bin",
        kind="data",
        path_in_workspace="data.bin",
        created_in_thread="t-1",
    )
    assert version.size_bytes is None

    await store.set_version_digest(version_id=version.id, size_bytes=128, sha256="abc123")

    latest = await store.get_latest_version(tenant_id=tenant_id, user_id=user_id, name="data.bin")
    assert latest is not None
    assert latest.size_bytes == 128
    assert latest.sha256 == "abc123"
