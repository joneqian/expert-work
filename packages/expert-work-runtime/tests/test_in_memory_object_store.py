"""Unit tests for :class:`InMemoryObjectStore`."""

from __future__ import annotations

import pytest

from expert_work.runtime.storage import InMemoryObjectStore, ObjectNotFoundError


@pytest.mark.asyncio
async def test_put_then_get_round_trip() -> None:
    store = InMemoryObjectStore()
    await store.put("t1/uploads/foo.txt", b"hello")

    assert await store.get("t1/uploads/foo.txt") == b"hello"


@pytest.mark.asyncio
async def test_get_missing_raises_object_not_found() -> None:
    store = InMemoryObjectStore()
    with pytest.raises(ObjectNotFoundError):
        await store.get("does/not/exist")


@pytest.mark.asyncio
async def test_put_overwrites_existing_key() -> None:
    store = InMemoryObjectStore()
    await store.put("k", b"v1")
    await store.put("k", b"v2")

    assert await store.get("k") == b"v2"


@pytest.mark.asyncio
async def test_delete_is_idempotent() -> None:
    store = InMemoryObjectStore()
    await store.put("k", b"v")
    await store.delete("k")
    # Second delete must not raise.
    await store.delete("k")

    with pytest.raises(ObjectNotFoundError):
        await store.get("k")


@pytest.mark.asyncio
async def test_list_prefix_returns_sorted_matches() -> None:
    store = InMemoryObjectStore()
    await store.put("t1/uploads/c.txt", b"c")
    await store.put("t1/uploads/a.txt", b"a")
    await store.put("t1/uploads/b.txt", b"b")
    await store.put("t2/uploads/x.txt", b"x")

    listed = await store.list_prefix("t1/uploads/")
    assert listed == [
        "t1/uploads/a.txt",
        "t1/uploads/b.txt",
        "t1/uploads/c.txt",
    ]


@pytest.mark.asyncio
async def test_list_prefix_empty_when_no_matches() -> None:
    store = InMemoryObjectStore()
    await store.put("a", b"a")
    assert await store.list_prefix("missing/") == []


@pytest.mark.asyncio
async def test_presigned_url_includes_key_and_method() -> None:
    store = InMemoryObjectStore()
    url = await store.presigned_url("t1/uploads/foo.txt", method="PUT", expires_in=60)
    assert "t1/uploads/foo.txt" in url
    assert "method=PUT" in url
    assert "expires_in=60" in url


@pytest.mark.asyncio
async def test_metadata_is_stored_but_does_not_change_get() -> None:
    """``get`` returns only bytes; metadata is opaque to the M0 surface."""
    store = InMemoryObjectStore()
    await store.put("k", b"v", content_type="text/plain", metadata={"owner": "alice"})
    assert await store.get("k") == b"v"
