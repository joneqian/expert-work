"""Stream J.15-补强-2 — restore drill for the volume backup pipeline.

End-to-end: write a tar.gz blob via the lifecycle manager's archive
path into an in-memory ObjectStore, then run ``restore_volume_from_object``
+ ``restore_latest_archive_to_volume`` (with a writer callback that
avoids docker — same pattern as K15 pg-restore drill) and assert the
pulled bytes match.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from tools.persistence.restore_volume import (
    _format_new_volume_name,
    _select_latest_archive_key,
    restore_latest_archive_to_volume,
    restore_volume_from_object,
)

from expert_work.runtime.storage import InMemoryObjectStore


@pytest.mark.asyncio
async def test_restore_volume_from_object_pulls_bytes_to_writer() -> None:
    store = InMemoryObjectStore()
    key = "volume-archive/abc/def/expert-work-ws-x.tar.gz"
    payload = b"FAKE-TAR-GZ-12345"
    await store.put(key, payload, content_type="application/gzip")

    captured: dict[str, bytes] = {}

    async def _writer(volume_name: str, blob: bytes) -> None:
        captured[volume_name] = blob

    report = await restore_volume_from_object(
        object_store=store,
        object_key=key,
        new_volume_name="expert-work-ws-x_restored_drill",
        writer=_writer,
    )

    assert report.object_key == key
    assert report.new_volume_name == "expert-work-ws-x_restored_drill"
    assert report.size_bytes == len(payload)
    assert captured["expert-work-ws-x_restored_drill"] == payload


@pytest.mark.asyncio
async def test_select_latest_archive_prefers_archive_over_backup() -> None:
    """When both J-36 archive and J-29 backup exist, archive wins."""
    store = InMemoryObjectStore()
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    user_id = UUID("22222222-2222-2222-2222-222222222222")

    archive_key = f"volume-archive/{tenant_id}/{user_id}/v.tar.gz"
    backup_key = f"volume-backups/{tenant_id}/{user_id}/2026-05-21/v.tar.gz"
    await store.put(archive_key, b"ARCHIVE", content_type="application/gzip")
    await store.put(backup_key, b"BACKUP", content_type="application/gzip")

    picked = await _select_latest_archive_key(
        object_store=store,
        tenant_id=tenant_id,
        user_id=user_id,
        archive_prefix="volume-archive",
        backup_prefix="volume-backups",
    )
    assert picked == archive_key


@pytest.mark.asyncio
async def test_select_latest_archive_falls_back_to_dated_backup() -> None:
    store = InMemoryObjectStore()
    tenant_id, user_id = uuid4(), uuid4()
    older = f"volume-backups/{tenant_id}/{user_id}/2026-05-20/v.tar.gz"
    newer = f"volume-backups/{tenant_id}/{user_id}/2026-05-21/v.tar.gz"
    await store.put(older, b"OLDER", content_type="application/gzip")
    await store.put(newer, b"NEWER", content_type="application/gzip")

    # No date pinned → lexicographically latest (2026-05-21 > 2026-05-20).
    picked = await _select_latest_archive_key(
        object_store=store,
        tenant_id=tenant_id,
        user_id=user_id,
        archive_prefix="volume-archive",
        backup_prefix="volume-backups",
    )
    assert picked == newer

    # Date pinned → that day's key.
    pinned = await _select_latest_archive_key(
        object_store=store,
        tenant_id=tenant_id,
        user_id=user_id,
        archive_prefix="volume-archive",
        backup_prefix="volume-backups",
        date="2026-05-20",
    )
    assert pinned == older


@pytest.mark.asyncio
async def test_select_latest_archive_returns_none_when_empty() -> None:
    store = InMemoryObjectStore()
    picked = await _select_latest_archive_key(
        object_store=store,
        tenant_id=uuid4(),
        user_id=uuid4(),
        archive_prefix="volume-archive",
        backup_prefix="volume-backups",
    )
    assert picked is None


def test_format_new_volume_name_is_deterministic() -> None:
    assert _format_new_volume_name("expert-work-ws-x", suffix="manual") == (
        "expert-work-ws-x_restored_manual"
    )
    assert _format_new_volume_name("v", suffix="2026-05-21") == "v_restored_2026-05-21"


@pytest.mark.asyncio
async def test_restore_latest_archive_raises_when_no_artifact() -> None:
    store = InMemoryObjectStore()
    with pytest.raises(RuntimeError, match="no archive or backup found"):
        await restore_latest_archive_to_volume(
            object_store=store,
            tenant_id=uuid4(),
            user_id=uuid4(),
            archive_prefix="volume-archive",
            backup_prefix="volume-backups",
            image="expert-work-sandbox:dev",
        )
