"""Unit tests for :class:`InMemoryImageUploadStore` — Stream J.6.补强-3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from expert_work.persistence import InMemoryImageUploadStore


@pytest.mark.asyncio
async def test_insert_and_get_round_trip() -> None:
    store = InMemoryImageUploadStore()
    tenant = uuid4()
    image_id = uuid4()
    row = await store.insert(
        image_id=image_id,
        tenant_id=tenant,
        thread_id=uuid4(),
        user_id=uuid4(),
        object_key="tenants/x/uploads/y.png",
        size_bytes=128,
        mime_type="image/png",
        sha256="deadbeef" * 8,
    )
    assert row.id == image_id
    fetched = await store.get(image_id=image_id, tenant_id=tenant)
    assert fetched is not None
    assert fetched.deleted_at is None
    assert fetched.size_bytes == 128


@pytest.mark.asyncio
async def test_get_returns_none_for_other_tenant() -> None:
    """Cross-tenant probe returns None — never raise."""
    store = InMemoryImageUploadStore()
    image_id = uuid4()
    await store.insert(
        image_id=image_id,
        tenant_id=uuid4(),
        thread_id=uuid4(),
        user_id=None,
        object_key="x",
        size_bytes=1,
        mime_type="image/png",
        sha256="x",
    )
    assert await store.get(image_id=image_id, tenant_id=uuid4()) is None


@pytest.mark.asyncio
async def test_soft_delete_flips_deleted_at() -> None:
    store = InMemoryImageUploadStore()
    tenant = uuid4()
    image_id = uuid4()
    await store.insert(
        image_id=image_id,
        tenant_id=tenant,
        thread_id=uuid4(),
        user_id=None,
        object_key="x",
        size_bytes=1,
        mime_type="image/png",
        sha256="x",
    )
    now = datetime.now(UTC)
    assert await store.soft_delete(image_id=image_id, tenant_id=tenant, now=now)
    row = await store.get(image_id=image_id, tenant_id=tenant)
    assert row is not None and row.deleted_at == now
    # Second call is a no-op.
    assert not await store.soft_delete(image_id=image_id, tenant_id=tenant, now=now)


@pytest.mark.asyncio
async def test_list_active_for_thread_excludes_soft_deleted() -> None:
    store = InMemoryImageUploadStore()
    tenant = uuid4()
    thread = uuid4()
    alive_id = uuid4()
    dead_id = uuid4()
    for image_id in (alive_id, dead_id):
        await store.insert(
            image_id=image_id,
            tenant_id=tenant,
            thread_id=thread,
            user_id=None,
            object_key=f"k-{image_id}",
            size_bytes=1,
            mime_type="image/png",
            sha256="x",
        )
    await store.soft_delete(image_id=dead_id, tenant_id=tenant, now=datetime.now(UTC))

    active = await store.list_active_for_thread(tenant_id=tenant, thread_id=thread)
    assert [r.id for r in active] == [alive_id]


@pytest.mark.asyncio
async def test_list_expired_and_hard_delete() -> None:
    """``list_expired`` finds rows older than the cutoff regardless of
    soft-delete state; ``hard_delete`` purges them by id."""
    store = InMemoryImageUploadStore()
    tenant = uuid4()
    image_id = uuid4()
    await store.insert(
        image_id=image_id,
        tenant_id=tenant,
        thread_id=uuid4(),
        user_id=None,
        object_key="x",
        size_bytes=1,
        mime_type="image/png",
        sha256="x",
    )

    future_cutoff = datetime.now(UTC) + timedelta(days=1)
    expired = await store.list_expired(before=future_cutoff)
    assert [r.id for r in expired] == [image_id]

    removed = await store.hard_delete(image_ids=[image_id])
    assert removed == 1
    assert await store.get(image_id=image_id, tenant_id=tenant) is None
    # Idempotent — re-deleting reports 0.
    assert await store.hard_delete(image_ids=[image_id]) == 0
