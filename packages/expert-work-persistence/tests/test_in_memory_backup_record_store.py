"""Unit tests for :class:`InMemoryBackupRecordStore`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from expert_work.persistence.dr import InMemoryBackupRecordStore
from expert_work.protocol import (
    BackupAssetType,
    BackupRecord,
    BackupStatus,
    BackupTier,
    DrillRecord,
    DrillType,
)


def _record(
    *,
    asset_type: BackupAssetType = BackupAssetType.POSTGRES_FULL,
    asset_ref: str = "s3://expert-work-dev/backups/postgres/2026-05-12.dump",
    started_at: datetime | None = None,
    status: BackupStatus = BackupStatus.RUNNING,
    finished_at: datetime | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    error: str | None = None,
) -> BackupRecord:
    return BackupRecord(
        asset_type=asset_type,
        asset_ref=asset_ref,
        started_at=started_at or datetime.now(UTC),
        finished_at=finished_at,
        size_bytes=size_bytes,
        sha256=sha256,
        status=status,
        error=error,
        region="cn-hangzhou",
        tier=BackupTier.TIER_0,
    )


@pytest.mark.asyncio
async def test_first_insert_assigns_id() -> None:
    store = InMemoryBackupRecordStore()
    written = await store.record(_record())
    assert written.id == 1


@pytest.mark.asyncio
async def test_status_transition_preserves_id() -> None:
    """``BackupJob`` writes RUNNING first, then SUCCESS/FAILED with the
    same asset_ref — both calls must map to the same row id."""
    store = InMemoryBackupRecordStore()
    initial = await store.record(_record(status=BackupStatus.RUNNING))

    finalized = await store.record(
        _record(
            status=BackupStatus.SUCCESS,
            finished_at=datetime.now(UTC),
            size_bytes=42 * 1024,
            sha256="a" * 64,
        )
    )

    assert finalized.id == initial.id
    assert finalized.status == BackupStatus.SUCCESS
    assert finalized.size_bytes == 42 * 1024


@pytest.mark.asyncio
async def test_different_asset_refs_get_separate_ids() -> None:
    store = InMemoryBackupRecordStore()
    a = await store.record(_record(asset_ref="s3://b/2026-05-11.dump"))
    b = await store.record(_record(asset_ref="s3://b/2026-05-12.dump"))
    assert a.id != b.id


@pytest.mark.asyncio
async def test_latest_returns_newest_by_started_at() -> None:
    store = InMemoryBackupRecordStore()
    older = datetime(2026, 5, 1, tzinfo=UTC)
    newer = datetime(2026, 5, 11, tzinfo=UTC)
    await store.record(_record(asset_ref="r1", started_at=older))
    await store.record(_record(asset_ref="r2", started_at=newer))

    latest = await store.latest(BackupAssetType.POSTGRES_FULL)
    assert latest is not None
    assert latest.asset_ref == "r2"


@pytest.mark.asyncio
async def test_latest_filters_by_asset_type() -> None:
    store = InMemoryBackupRecordStore()
    await store.record(_record(asset_type=BackupAssetType.POSTGRES_FULL, asset_ref="pg"))
    await store.record(_record(asset_type=BackupAssetType.VAULT_SNAPSHOT, asset_ref="vault"))

    vault = await store.latest(BackupAssetType.VAULT_SNAPSHOT)
    assert vault is not None
    assert vault.asset_ref == "vault"


@pytest.mark.asyncio
async def test_latest_returns_none_when_no_backups() -> None:
    store = InMemoryBackupRecordStore()
    assert await store.latest(BackupAssetType.POSTGRES_FULL) is None


@pytest.mark.asyncio
async def test_record_drill_assigns_id() -> None:
    store = InMemoryBackupRecordStore()
    started = datetime.now(UTC)
    drill = DrillRecord(
        drill_type=DrillType.RESTORE_POSTGRES,
        started_at=started,
        finished_at=started + timedelta(hours=2),
        rpo_actual_s=3600,
        rto_actual_s=2 * 3600,
        target_rpo_s=24 * 3600,
        target_rto_s=4 * 3600,
        passed=True,
        notes="M0 first drill",
    )

    written = await store.record_drill(drill)
    assert written.id is not None
    assert written.passed is True
    assert store.snapshot_drills()[0] == written
