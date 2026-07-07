"""Tests for :mod:`expert_work.runtime.skill_assets` — skill-asset-store."""

from __future__ import annotations

import base64
import hashlib

import pytest

from expert_work.protocol.skill import SkillSupportingFile
from expert_work.runtime.skill_assets import (
    ASSET_KEY_PREFIX,
    SkillAssetIntegrityError,
    SkillAssetUnavailableError,
    externalize_supporting_files,
    fetch_supporting_file,
    fetch_supporting_files,
)


class _FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls = 0

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        self.put_calls += 1
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        return self.objects[key]


def _inline(raw: bytes, mime: str = "text/plain") -> SkillSupportingFile:
    return SkillSupportingFile(
        content=base64.b64encode(raw).decode("ascii"), size=len(raw), mime=mime
    )


@pytest.mark.asyncio
async def test_externalize_round_trip() -> None:
    store = _FakeStore()
    files = {"scripts/run.py": _inline(b"print('hi')"), "a.png": _inline(b"\x89PNG", "image/png")}
    external = await externalize_supporting_files(files, object_store=store)

    for path, sf in external.items():
        assert sf.is_external, path
        assert sf.content == ""
        assert sf.storage_key is not None and sf.storage_key.startswith(ASSET_KEY_PREFIX)
        assert sf.size == files[path].size
        assert sf.mime == files[path].mime
    # Round trip through the dual reader returns the original bytes.
    assert await fetch_supporting_file(external["scripts/run.py"], object_store=store) == (
        b"print('hi')"
    )


@pytest.mark.asyncio
async def test_externalize_is_content_addressed_and_idempotent() -> None:
    store = _FakeStore()
    files = {"a.txt": _inline(b"same"), "b.txt": _inline(b"same")}
    external = await externalize_supporting_files(files, object_store=store)
    # Identical bytes → identical key → one object.
    assert external["a.txt"].storage_key == external["b.txt"].storage_key
    assert len(store.objects) == 1
    # Re-externalizing an already-external mapping is a no-op.
    again = await externalize_supporting_files(external, object_store=store)
    assert again == external


@pytest.mark.asyncio
async def test_externalize_without_store_is_identity() -> None:
    files = {"a.txt": _inline(b"x")}
    assert await externalize_supporting_files(files, object_store=None) == files


@pytest.mark.asyncio
async def test_fetch_inline_entry_needs_no_store() -> None:
    assert await fetch_supporting_file(_inline(b"data"), object_store=None) == b"data"


@pytest.mark.asyncio
async def test_fetch_external_verifies_digest() -> None:
    store = _FakeStore()
    external = await externalize_supporting_files({"a.txt": _inline(b"good")}, object_store=store)
    entry = external["a.txt"]
    assert entry.storage_key is not None
    # Corrupt the stored object → integrity error, never silent bad bytes.
    store.objects[entry.storage_key] = b"evil"
    with pytest.raises(SkillAssetIntegrityError):
        await fetch_supporting_file(entry, object_store=store)


@pytest.mark.asyncio
async def test_fetch_external_without_store_raises() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    entry = SkillSupportingFile(
        content="", size=1, mime="", storage_key=f"{ASSET_KEY_PREFIX}{digest}", sha256=digest
    )
    with pytest.raises(SkillAssetUnavailableError):
        await fetch_supporting_file(entry, object_store=None)


@pytest.mark.asyncio
async def test_fetch_batch_mixed_shapes() -> None:
    store = _FakeStore()
    external = await externalize_supporting_files(
        {"big.bin": _inline(b"\x00" * 64)}, object_store=store
    )
    files = {"small.md": _inline(b"# hi"), **external}
    raw = await fetch_supporting_files(files, object_store=store)
    assert raw == {"small.md": b"# hi", "big.bin": b"\x00" * 64}
