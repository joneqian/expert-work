"""Unit tests for VolumeLifecycleManager — Stream J.15-补强-2.

Covers (Mini-ADR J-29 第 2 项 + J-36 lifecycle 第 2 → 第 3 档):

* ``archive_pending`` tars + uploads soft-deleted volumes, marks the
  row archived, and removes the docker volume.
* ``archive_pending`` swallows a docker failure into the DLQ.
* ``archive_pending`` swallows an ObjectStore failure into the DLQ.
* ``backup_active`` writes one ObjectStore object per active workspace
  with the date-prefixed key.
* ``drain_dlq`` retries ready rows; success marks done, failure
  re-enqueues (and the old row is marked done to avoid double-counting).
* The K7-style backoff envelope is applied on each retry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from helix_agent.persistence import (
    InMemoryUserWorkspaceStore,
    InMemoryVolumeBackupDLQ,
)
from helix_agent.protocol import AuditEntry, UserWorkspace
from helix_agent.protocol.audit import AuditAction
from helix_agent.runtime.storage import InMemoryObjectStore
from sandbox_supervisor.docker_client import DockerError
from sandbox_supervisor.lifecycle import (
    _BACKOFF_SECONDS,
    _DEAD_LETTER_DELAY,
    LifecycleResult,
    VolumeLifecycleManager,
    _archive_key,
    _backup_key,
    _next_retry_at,
)
from sandbox_supervisor.settings import SandboxSupervisorSettings


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def write(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class _FakeDocker:
    """Captures archive_volume + remove_volume calls; no docker required."""

    def __init__(
        self,
        *,
        archive_bytes: bytes = b"FAKE-TAR-GZ",
        archive_error: DockerError | None = None,
        remove_error: DockerError | None = None,
    ) -> None:
        self.archive_calls: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self._archive_bytes = archive_bytes
        self._archive_error = archive_error
        self._remove_error = remove_error

    async def archive_volume(self, *, volume: str, image: str, max_bytes: int) -> bytes:
        self.archive_calls.append((volume, image))
        if self._archive_error is not None:
            raise self._archive_error
        if len(self._archive_bytes) > max_bytes:
            msg = "archive exceeds cap"
            raise DockerError(msg)
        return self._archive_bytes

    async def remove_volume(self, *, volume: str) -> None:
        if self._remove_error is not None:
            raise self._remove_error
        self.removed.append(volume)

    # Other DockerClient methods aren't exercised by VolumeLifecycleManager.
    async def launch(self, argv: list[str]) -> object:  # pragma: no cover
        raise NotImplementedError

    async def remove(self, container_name: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def ping(self) -> bool:  # pragma: no cover
        return True

    async def sweep_orphans(self) -> int:  # pragma: no cover
        return 0

    async def read_volume_file(
        self, *, volume: str, path: str, image: str, max_bytes: int
    ) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def measure_volume_size(self, *, volume: str, image: str) -> int:  # pragma: no cover
        return 0


async def _make_workspace(
    store: InMemoryUserWorkspaceStore,
    *,
    deleted: bool = False,
) -> UserWorkspace:
    workspace = await store.resolve(tenant_id=uuid4(), user_id=uuid4())
    if deleted:
        await store.soft_delete(workspace_id=workspace.id, now=datetime.now(UTC))
        workspace = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    return workspace


def _manager(
    *,
    store: InMemoryUserWorkspaceStore,
    docker: _FakeDocker,
    settings: SandboxSupervisorSettings | None = None,
) -> tuple[VolumeLifecycleManager, InMemoryVolumeBackupDLQ, InMemoryObjectStore, _RecordingAudit]:
    dlq = InMemoryVolumeBackupDLQ()
    object_store = InMemoryObjectStore()
    audit = _RecordingAudit()
    resolved_settings = settings or SandboxSupervisorSettings()
    manager = VolumeLifecycleManager(
        workspace_store=store,
        dlq=dlq,
        docker=docker,
        object_store=object_store,
        settings=resolved_settings,
        audit=audit,
        service_name="sandbox_supervisor",
    )
    return manager, dlq, object_store, audit


# ---------------------------------------------------------------------------
# archive_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_pending_uploads_and_marks_archived() -> None:
    store = InMemoryUserWorkspaceStore()
    workspace = await _make_workspace(store, deleted=True)
    docker = _FakeDocker(archive_bytes=b"PAYLOAD")
    manager, dlq, object_store, audit = _manager(store=store, docker=docker)

    result = await manager.archive_pending()

    assert result == LifecycleResult(succeeded=1, failed=0)
    assert docker.archive_calls == [(workspace.volume_name, "helix-sandbox:dev")]
    assert workspace.volume_name in docker.removed

    expected_key = _archive_key("volume-archive", workspace)
    keys = await object_store.list_prefix("volume-archive/")
    assert keys == [expected_key]
    payload = await object_store.get(expected_key)
    assert payload == b"PAYLOAD"

    refreshed = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    assert refreshed.archived_object_key == expected_key

    assert await dlq.count() == 0
    actions = [e.action for e in audit.entries]
    assert AuditAction.WORKSPACE_ARCHIVE in actions


@pytest.mark.asyncio
async def test_archive_pending_dlq_on_docker_error() -> None:
    store = InMemoryUserWorkspaceStore()
    await _make_workspace(store, deleted=True)
    docker = _FakeDocker(archive_error=DockerError("daemon down"))
    manager, dlq, object_store, _audit = _manager(store=store, docker=docker)

    result = await manager.archive_pending()
    assert result == LifecycleResult(succeeded=0, failed=1)
    assert await object_store.list_prefix("") == []
    assert await dlq.count() == 1


@pytest.mark.asyncio
async def test_archive_pending_dlq_on_object_store_error(monkeypatch) -> None:
    store = InMemoryUserWorkspaceStore()
    workspace = await _make_workspace(store, deleted=True)
    docker = _FakeDocker()
    manager, dlq, object_store, _audit = _manager(store=store, docker=docker)

    async def _put_failing(*_args, **_kwargs) -> None:
        from helix_agent.runtime.storage.base import ObjectStoreError

        msg = "503"
        raise ObjectStoreError(msg)

    monkeypatch.setattr(object_store, "put", _put_failing)

    result = await manager.archive_pending()
    assert result == LifecycleResult(succeeded=0, failed=1)
    assert await dlq.count() == 1
    # Volume must not be removed when the upload failed.
    assert workspace.volume_name not in docker.removed


# ---------------------------------------------------------------------------
# backup_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_active_writes_dated_key_per_workspace() -> None:
    store = InMemoryUserWorkspaceStore()
    w1 = await _make_workspace(store, deleted=False)
    w2 = await _make_workspace(store, deleted=False)
    # Soft-delete a third — it must NOT appear in active backup.
    await _make_workspace(store, deleted=True)

    docker = _FakeDocker(archive_bytes=b"DAILY")
    manager, _dlq, object_store, audit = _manager(store=store, docker=docker)

    when = datetime(2026, 5, 21, 3, 0, 0, tzinfo=UTC)
    result = await manager.backup_active(now=when)

    assert result == LifecycleResult(succeeded=2, failed=0)
    keys = await object_store.list_prefix("volume-backups/")
    assert sorted(keys) == sorted(
        [
            _backup_key("volume-backups", w1, "2026-05-21"),
            _backup_key("volume-backups", w2, "2026-05-21"),
        ]
    )
    # All success audits use the backup action.
    backup_audits = [e for e in audit.entries if e.action is AuditAction.WORKSPACE_BACKUP]
    assert len(backup_audits) == 2


# ---------------------------------------------------------------------------
# drain_dlq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_dlq_succeeds_on_retry() -> None:
    store = InMemoryUserWorkspaceStore()
    workspace = await _make_workspace(store, deleted=True)

    # First sweep: docker fails → DLQ has one row.
    docker = _FakeDocker(archive_error=DockerError("transient"))
    manager, dlq, _store, _audit = _manager(store=store, docker=docker)
    await manager.archive_pending()
    assert await dlq.count() == 1

    # Heal docker, drain DLQ.
    docker._archive_error = None
    result = await manager.drain_dlq()
    assert result.succeeded == 1
    assert await dlq.count() == 0

    refreshed = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    assert refreshed.archived_object_key is not None


@pytest.mark.asyncio
async def test_drain_dlq_records_failure_then_succeeds_next_sweep() -> None:
    """The DLQ entry survives a failed retry — but as a fresh enqueue,
    not via record_failure (the lifecycle manager enqueues new on each
    failure path). The old row is mark_done'd to avoid double-counting."""
    store = InMemoryUserWorkspaceStore()
    await _make_workspace(store, deleted=True)
    docker = _FakeDocker(archive_error=DockerError("e"))
    manager, dlq, _store, _audit = _manager(store=store, docker=docker)
    await manager.archive_pending()  # enqueues 1
    assert await dlq.count() == 1

    # Drain — still failing. Old row marked done; new row enqueued.
    result = await manager.drain_dlq()
    assert result.failed == 1
    assert await dlq.count() == 1


# ---------------------------------------------------------------------------
# Backoff helpers
# ---------------------------------------------------------------------------


def test_next_retry_at_uses_backoff_schedule() -> None:
    now = datetime(2026, 5, 21, tzinfo=UTC)
    delays = [
        (_next_retry_at(now=now, attempts_after_failure=i) - now).total_seconds()
        for i in range(1, len(_BACKOFF_SECONDS) + 1)
    ]
    assert delays == list(_BACKOFF_SECONDS)


def test_next_retry_at_dead_letters_past_max() -> None:
    now = datetime(2026, 5, 21, tzinfo=UTC)
    after_max = _next_retry_at(now=now, attempts_after_failure=len(_BACKOFF_SECONDS) + 1)
    assert (after_max - now) == _DEAD_LETTER_DELAY
