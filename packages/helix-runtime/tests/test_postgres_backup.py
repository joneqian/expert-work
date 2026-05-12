"""Unit tests for :class:`PostgresFullBackup` using injected dump_fn.

End-to-end test against a real ``pg_dump`` + MinIO lives in
``test_postgres_backup_integration.py`` and is marked ``integration``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from helix_agent.persistence.dr import InMemoryBackupRecordStore
from helix_agent.protocol import (
    BackupAssetType,
    BackupRecord,
    BackupStatus,
)
from helix_agent.runtime.dr import (
    BackupError,
    PostgresBackupConfig,
    PostgresFullBackup,
)
from helix_agent.runtime.dr.postgres_backup import DumpFn
from helix_agent.runtime.storage import InMemoryObjectStore


def _config() -> PostgresBackupConfig:
    return PostgresBackupConfig(
        dsn="postgresql://test:test@127.0.0.1:0/test",  # never opened
        bucket_prefix="backups/postgres",
        region="local",
    )


def _fake_dump_writer(payload: bytes) -> DumpFn:
    async def _write(target: Path) -> None:
        target.write_bytes(payload)

    return _write


def _failing_dump_writer(message: str) -> DumpFn:
    async def _fail(_target: Path) -> None:
        raise BackupError(message)

    return _fail


@pytest.mark.asyncio
async def test_run_writes_running_then_success_record() -> None:
    objects = InMemoryObjectStore()
    records = InMemoryBackupRecordStore()

    job = PostgresFullBackup(
        config=_config(),
        object_store=objects,
        record_store=records,
        dump_fn=_fake_dump_writer(b"FAKE PG DUMP CONTENT"),
        clock=lambda: datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC),
    )
    record = await job.run()

    assert record.status == BackupStatus.SUCCESS
    assert record.size_bytes == len(b"FAKE PG DUMP CONTENT")
    assert record.sha256 is not None
    assert len(record.sha256) == 64
    assert record.asset_ref == "backups/postgres/20260512T120000Z.dump"
    assert await objects.get(record.asset_ref) == b"FAKE PG DUMP CONTENT"


@pytest.mark.asyncio
async def test_latest_record_carries_success_after_run() -> None:
    """The freshness alert (``store.latest(...)``) reads ``SUCCESS`` after a
    successful run — even though the job writes ``RUNNING`` first, the
    upsert replaces it in place."""
    objects = InMemoryObjectStore()
    records = InMemoryBackupRecordStore()
    job = PostgresFullBackup(
        config=_config(),
        object_store=objects,
        record_store=records,
        dump_fn=_fake_dump_writer(b"x"),
    )
    await job.run()

    latest = await records.latest(BackupAssetType.POSTGRES_FULL)
    assert latest is not None
    assert latest.status == BackupStatus.SUCCESS


@pytest.mark.asyncio
async def test_run_writes_failed_record_and_raises() -> None:
    objects = InMemoryObjectStore()
    records = InMemoryBackupRecordStore()
    job = PostgresFullBackup(
        config=_config(),
        object_store=objects,
        record_store=records,
        dump_fn=_failing_dump_writer("pg_dump exited 1: missing db"),
    )

    with pytest.raises(BackupError, match="missing db"):
        await job.run()

    latest = await records.latest(BackupAssetType.POSTGRES_FULL)
    assert latest is not None
    assert latest.status == BackupStatus.FAILED
    assert latest.error is not None
    assert "missing db" in latest.error


@pytest.mark.asyncio
async def test_run_writes_failed_record_when_upload_fails() -> None:
    """Object-store failure path: dump produced, but ``put`` raises."""

    class BrokenStore(InMemoryObjectStore):
        async def put(self, *args: object, **kwargs: object) -> None:
            msg = "S3 503 SlowDown"
            raise RuntimeError(msg)

    records = InMemoryBackupRecordStore()
    job = PostgresFullBackup(
        config=_config(),
        object_store=BrokenStore(),
        record_store=records,
        dump_fn=_fake_dump_writer(b"x"),
    )

    with pytest.raises(BackupError, match="SlowDown"):
        await job.run()

    latest = await records.latest(BackupAssetType.POSTGRES_FULL)
    assert latest is not None
    assert latest.status == BackupStatus.FAILED


@pytest.mark.asyncio
async def test_running_record_visible_before_completion() -> None:
    """While ``run()`` is in flight, the latest record carries
    ``status=RUNNING``. Operators / freshness alerts can see the attempt."""
    objects = InMemoryObjectStore()
    records = InMemoryBackupRecordStore()

    observed: list[BackupRecord | None] = []

    async def _slow_dump(target: Path) -> None:
        snapshot = await records.latest(BackupAssetType.POSTGRES_FULL)
        observed.append(snapshot)
        target.write_bytes(b"ok")

    job = PostgresFullBackup(
        config=_config(),
        object_store=objects,
        record_store=records,
        dump_fn=_slow_dump,
    )
    await job.run()

    assert observed[0] is not None
    assert observed[0].status == BackupStatus.RUNNING


@pytest.mark.asyncio
async def test_dump_temp_file_cleaned_up_on_success() -> None:
    """The temp ``pg_dump`` artifact is removed after upload — leaving these
    files in /tmp would leak the DB contents to anyone with shell access."""
    captured_paths: list[Path] = []

    async def _capture(target: Path) -> None:
        captured_paths.append(target)
        target.write_bytes(b"ok")

    job = PostgresFullBackup(
        config=_config(),
        object_store=InMemoryObjectStore(),
        record_store=InMemoryBackupRecordStore(),
        dump_fn=_capture,
    )
    await job.run()

    assert captured_paths and not captured_paths[0].exists()
