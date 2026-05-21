"""Unit tests for :class:`QuotaEnforcer` — Stream J.15-补强-1.

Covers (Mini-ADR J-29 第 1 项 + J-36):

* ``check`` raises :class:`WorkspaceQuotaExceededError` when
  ``size_bytes >= size_limit_bytes`` and emits the deny audit.
* ``check`` raises :class:`WorkspaceDeletedError` when the workspace is
  soft-deleted and emits the deny audit.
* ``check`` passes silently when the workspace is well under quota.
* ``refresh_size`` writes the latest measurement back to the store.
* ``refresh_size`` swallows :class:`DockerError` and continues.
* ``refresh_size`` swallows :class:`WorkspaceNotFoundError` (row was
  hard-deleted between resolve and refresh).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from helix_agent.persistence import InMemoryUserWorkspaceStore, WorkspaceNotFoundError
from helix_agent.protocol import AuditEntry, UserWorkspace
from helix_agent.protocol.audit import AuditAction, AuditResult
from sandbox_supervisor.docker_client import DockerError
from sandbox_supervisor.domain import (
    WorkspaceDeletedError,
    WorkspaceQuotaExceededError,
)
from sandbox_supervisor.quota_enforcer import QuotaEnforcer


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def write(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class _FakeDocker:
    """Minimal :class:`DockerClient` fake — only ``measure_volume_size`` matters."""

    def __init__(self, *, size: int = 0, error: DockerError | None = None) -> None:
        self._size = size
        self._error = error
        self.measure_calls: list[tuple[str, str]] = []

    async def measure_volume_size(self, *, volume: str, image: str) -> int:
        self.measure_calls.append((volume, image))
        if self._error is not None:
            raise self._error
        return self._size

    # The remaining DockerClient methods aren't exercised by QuotaEnforcer.
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


async def _make_workspace(
    *, size_bytes: int = 0, size_limit_bytes: int = 1024
) -> tuple[InMemoryUserWorkspaceStore, UserWorkspace]:
    store = InMemoryUserWorkspaceStore()
    workspace = await store.resolve(tenant_id=uuid4(), user_id=uuid4())
    # Override the default 10 GiB limit so tests don't have to fabricate huge size_bytes.
    await store.update_size(workspace_id=workspace.id, size_bytes=size_bytes)
    workspace = workspace.model_copy(
        update={"size_bytes": size_bytes, "size_limit_bytes": size_limit_bytes}
    )
    return store, workspace


def _enforcer(
    store: InMemoryUserWorkspaceStore, docker: _FakeDocker
) -> tuple[QuotaEnforcer, _RecordingAudit]:
    audit = _RecordingAudit()
    return (
        QuotaEnforcer(
            workspace_store=store,
            audit=audit,
            docker=docker,
            measure_image="helix-sandbox:dev",
            service_name="sandbox_supervisor",
        ),
        audit,
    )


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_passes_under_quota() -> None:
    store, workspace = await _make_workspace(size_bytes=512, size_limit_bytes=1024)
    enforcer, audit = _enforcer(store, _FakeDocker())

    await enforcer.check(workspace=workspace)

    assert audit.entries == []


@pytest.mark.asyncio
async def test_check_raises_when_size_equals_limit() -> None:
    store, workspace = await _make_workspace(size_bytes=1024, size_limit_bytes=1024)
    enforcer, audit = _enforcer(store, _FakeDocker())

    with pytest.raises(WorkspaceQuotaExceededError) as excinfo:
        await enforcer.check(workspace=workspace)

    assert excinfo.value.size_bytes == 1024
    assert excinfo.value.size_limit_bytes == 1024
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action is AuditAction.WORKSPACE_QUOTA_DENIED
    assert entry.result is AuditResult.DENIED
    assert entry.resource_type == "user_workspace"
    assert entry.resource_id == str(workspace.id)
    assert entry.details["size_bytes"] == 1024
    assert entry.details["size_limit_bytes"] == 1024


@pytest.mark.asyncio
async def test_check_raises_when_workspace_is_soft_deleted() -> None:
    store, workspace = await _make_workspace(size_bytes=10)
    await store.soft_delete(workspace_id=workspace.id, now=datetime.now(UTC))
    deleted = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    enforcer, audit = _enforcer(store, _FakeDocker())

    with pytest.raises(WorkspaceDeletedError) as excinfo:
        await enforcer.check(workspace=deleted)

    assert excinfo.value.workspace_id == workspace.id
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.action is AuditAction.WORKSPACE_QUOTA_DENIED
    assert entry.reason is not None
    assert "soft-deleted" in entry.reason


@pytest.mark.asyncio
async def test_check_soft_delete_takes_precedence_over_quota() -> None:
    # Even though the workspace is at quota, the soft-delete reject path
    # is the one that fires — recovery is the M1 concern, not quota.
    store, workspace = await _make_workspace(size_bytes=2048, size_limit_bytes=1024)
    await store.soft_delete(workspace_id=workspace.id, now=datetime.now(UTC))
    deleted = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    deleted_with_size = deleted.model_copy(update={"size_bytes": 2048, "size_limit_bytes": 1024})
    enforcer, _audit = _enforcer(store, _FakeDocker())

    with pytest.raises(WorkspaceDeletedError):
        await enforcer.check(workspace=deleted_with_size)


# ---------------------------------------------------------------------------
# refresh_size()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_size_writes_measure_to_store() -> None:
    store, workspace = await _make_workspace(size_bytes=0)
    docker = _FakeDocker(size=4096)
    enforcer, _audit = _enforcer(store, docker)

    await enforcer.refresh_size(workspace=workspace)

    refreshed = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    assert refreshed.size_bytes == 4096
    assert docker.measure_calls == [(workspace.volume_name, "helix-sandbox:dev")]


@pytest.mark.asyncio
async def test_refresh_size_swallows_docker_error() -> None:
    store, workspace = await _make_workspace(size_bytes=42)
    docker = _FakeDocker(error=DockerError("daemon down"))
    enforcer, _audit = _enforcer(store, docker)

    # Best-effort — exception must not propagate.
    await enforcer.refresh_size(workspace=workspace)

    # Store remains unchanged.
    refreshed = await store.resolve(tenant_id=workspace.tenant_id, user_id=workspace.user_id)
    assert refreshed.size_bytes == 42


@pytest.mark.asyncio
async def test_refresh_size_swallows_workspace_gone() -> None:
    # Simulate the race where the row is hard-deleted between resolve and refresh.
    store, workspace = await _make_workspace(size_bytes=0)
    docker = _FakeDocker(size=128)
    enforcer, _audit = _enforcer(store, docker)
    phantom = workspace.model_copy(update={"id": uuid4()})

    # The fake update_size raises WorkspaceNotFoundError for an unknown id;
    # refresh_size must swallow that.
    await enforcer.refresh_size(workspace=phantom)


@pytest.mark.asyncio
async def test_refresh_size_propagates_unknown_workspace_error_with_log_only() -> None:
    """Documenting the behavior: refresh_size never raises into the caller."""
    store, workspace = await _make_workspace(size_bytes=0)
    docker = _FakeDocker(size=128)
    enforcer, _audit = _enforcer(store, docker)
    phantom = workspace.model_copy(update={"id": uuid4()})

    try:
        await enforcer.refresh_size(workspace=phantom)
    except (WorkspaceNotFoundError, DockerError):  # pragma: no cover - regression guard
        pytest.fail("refresh_size must not raise; it should swallow + log")
