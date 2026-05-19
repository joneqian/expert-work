"""Unit tests for InMemoryArtifactStore — Stream J.9 contract."""

from __future__ import annotations

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
