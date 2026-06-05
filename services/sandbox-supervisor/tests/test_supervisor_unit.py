"""Unit tests for the Sandbox Supervisor — Stream F.1 + F.4a.

All Docker / DB dependencies are faked, so these run in the plain
``pytest`` job — no testcontainers. Groups:

* #40 — acquire / release lifecycle + state machine
* #41 — per-tenant quota denial + audit
* #42 — the TTL reaper
* F.4a — the held-pipe ``exec`` channel (option C)

Plus HTTP-route smoke tests over an injected supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from helix_agent.persistence import InMemoryUserWorkspaceStore, workspace_volume_name
from helix_agent.protocol.audit import AuditAction, AuditResult
from helix_agent.runtime.sandbox import SandboxRuntimeProvider
from sandbox_supervisor.app import create_app
from sandbox_supervisor.docker_client import DockerError
from sandbox_supervisor.domain import (
    DESTROY_REASON_IDLE_TIMEOUT,
    QuotaExceededError,
    SandboxNotFoundError,
    SandboxRecord,
    SandboxState,
    SupervisorError,
    WorkspaceDeletedError,
    WorkspaceFileNotFoundError,
    WorkspaceFileTooLargeError,
    WorkspaceQuotaExceededError,
)
from sandbox_supervisor.quota_enforcer import QuotaEnforcer
from sandbox_supervisor.reaper import SandboxReaper
from sandbox_supervisor.runner_link import ExecResult, RunnerLinkError
from sandbox_supervisor.schemas import AcquireRequest
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.supervisor import _MAX_ARTIFACT_BYTES, SandboxSupervisor

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRunnerLink:
    """A :class:`RunnerLink` that never launches a real container."""

    def __init__(
        self,
        *,
        ready: bool = True,
        exec_result: ExecResult | None = None,
        exec_error: RunnerLinkError | None = None,
    ) -> None:
        self._ready = ready
        self._exec_result = exec_result or ExecResult(
            stdout="ok", stderr="", exit_code=0, timed_out=False
        )
        self._exec_error = exec_error
        self.closed = False
        self.exec_calls: list[tuple[str, int]] = []

    async def wait_ready(self, timeout_s: float) -> None:
        if not self._ready:
            msg = "runner never reported ready"
            raise RunnerLinkError(msg)

    async def exec(self, code: str, timeout_s: int) -> ExecResult:
        self.exec_calls.append((code, timeout_s))
        if self._exec_error is not None:
            raise self._exec_error
        return self._exec_result

    async def close(self) -> None:
        self.closed = True


class RecordingDockerClient:
    """A :class:`DockerClient` that records calls and never touches Docker."""

    def __init__(
        self,
        *,
        launch_error: DockerError | None = None,
        link: FakeRunnerLink | None = None,
        volume_file: bytes = b"",
        volume_file_error: DockerError | None = None,
        measured_size: int = 0,
        measure_error: DockerError | None = None,
    ) -> None:
        self.launches: list[list[str]] = []
        self.removed: list[str] = []
        self.swept = 0
        self._launch_error = launch_error
        self._link = link
        self.links: list[FakeRunnerLink] = []
        self._volume_file = volume_file
        self._volume_file_error = volume_file_error
        self.volume_reads: list[tuple[str, str]] = []
        self._measured_size = measured_size
        self._measure_error = measure_error
        self.measure_calls: list[tuple[str, str]] = []

    async def launch(self, argv: list[str]) -> FakeRunnerLink:
        self.launches.append(argv)
        if self._launch_error is not None:
            raise self._launch_error
        link = self._link if self._link is not None else FakeRunnerLink()
        self.links.append(link)
        return link

    async def remove(self, container_name: str) -> None:
        self.removed.append(container_name)

    async def ping(self) -> bool:
        return True

    async def sweep_orphans(self) -> int:
        self.swept += 1
        return 0

    async def read_volume_file(
        self, *, volume: str, path: str, image: str, max_bytes: int
    ) -> bytes:
        del image, max_bytes
        self.volume_reads.append((volume, path))
        if self._volume_file_error is not None:
            raise self._volume_file_error
        return self._volume_file

    async def measure_volume_size(self, *, volume: str, image: str) -> int:
        self.measure_calls.append((volume, image))
        if self._measure_error is not None:
            raise self._measure_error
        return self._measured_size


class InMemorySandboxStore:
    """A :class:`SandboxStore` backed by a dict — no DB."""

    def __init__(self, *, limit: int | None = None) -> None:
        self.rows: dict[UUID, SandboxRecord] = {}
        self._limit = limit

    async def insert(self, record: SandboxRecord) -> None:
        self.rows[record.id] = record

    async def update(self, record: SandboxRecord) -> None:
        self.rows[record.id] = record

    async def get(self, sandbox_id: UUID) -> SandboxRecord | None:
        return self.rows.get(sandbox_id)

    async def count_active_for_tenant(self, tenant_id: UUID) -> int:
        return sum(
            1
            for r in self.rows.values()
            if r.tenant_id == tenant_id and r.state in (SandboxState.CREATING, SandboxState.IN_USE)
        )

    async def list_idle_sessions(self, *, now: datetime, idle_ttl_s: int) -> list[SandboxRecord]:
        return [
            r
            for r in self.rows.values()
            if r.state == SandboxState.IN_USE
            and (anchor := r.last_used_at or r.acquired_at) is not None
            and anchor + timedelta(seconds=idle_ttl_s) < now
        ]

    async def sandbox_limit_for_tenant(self, tenant_id: UUID) -> int | None:
        return self._limit

    def seed_active(self, tenant_id: UUID) -> SandboxRecord:
        """Insert an IN_USE row directly — for quota / reaper setup."""
        record = _running_record(tenant_id, acquired_at=datetime.now(UTC))
        self.rows[record.id] = record
        return record


class RecordingAuditSink:
    """An :class:`AuditSink` that collects entries in memory."""

    def __init__(self) -> None:
        self.entries: list = []

    async def write(self, entry: object) -> None:
        self.entries.append(entry)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class _Harness:
    supervisor: SandboxSupervisor
    store: InMemorySandboxStore
    docker: RecordingDockerClient
    audit: RecordingAuditSink
    workspaces: InMemoryUserWorkspaceStore


def _harness(
    *,
    store: InMemorySandboxStore | None = None,
    docker: RecordingDockerClient | None = None,
    settings: SandboxSupervisorSettings | None = None,
    quota_enabled: bool = False,
) -> _Harness:
    """Build a fake-backed supervisor harness.

    ``quota_enabled=True`` wires a real :class:`QuotaEnforcer` against
    the same fakes — used by the J.15-补强-1 acquire / release tests.
    Default ``False`` keeps the pre-existing tests unchanged (they pre-
    date workspace quota and don't expect ``measure_volume_size`` to be
    called).
    """
    resolved_store = store if store is not None else InMemorySandboxStore()
    resolved_docker = docker if docker is not None else RecordingDockerClient()
    audit = RecordingAuditSink()
    workspaces = InMemoryUserWorkspaceStore()
    resolved_settings = settings or SandboxSupervisorSettings()
    quota_enforcer = (
        QuotaEnforcer(
            workspace_store=workspaces,
            audit=audit,
            docker=resolved_docker,
            measure_image=resolved_settings.sandbox_image,
            service_name=resolved_settings.service_name,
        )
        if quota_enabled
        else None
    )
    supervisor = SandboxSupervisor(
        store=resolved_store,
        docker=resolved_docker,
        audit=audit,
        runtime_provider=SandboxRuntimeProvider(oci_runtime="runc"),
        workspace_store=workspaces,
        settings=resolved_settings,
        quota_enforcer=quota_enforcer,
    )
    return _Harness(supervisor, resolved_store, resolved_docker, audit, workspaces)


def _running_record(tenant_id: UUID, *, acquired_at: datetime) -> SandboxRecord:
    sandbox_id = uuid4()
    return SandboxRecord(
        id=sandbox_id,
        tenant_id=tenant_id,
        image_ref="helix-sandbox:dev",
        node="local",
        container_id=f"helix-sb-{sandbox_id}",
        state=SandboxState.IN_USE,
        thread_id="t-1",
        cpu_quota=1.0,
        memory_mb=512,
        pids_limit=128,
        timeout_s=30,
        created_at=acquired_at,
        acquired_at=acquired_at,
    )


def _acquire_request(
    tenant_id: UUID | None = None,
    *,
    user_id: UUID | None = None,
    image_variant: str | None = None,
) -> AcquireRequest:
    return AcquireRequest(
        tenant_id=tenant_id or uuid4(),
        thread_id="t-1",
        user_id=user_id,
        image_variant=image_variant,
    )


# ---------------------------------------------------------------------------
# #40 — acquire / release lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_launches_container_and_marks_in_use() -> None:
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())

    assert response.container_id == f"helix-sb-{response.sandbox_id}"
    assert response.cold_start is True
    assert len(h.docker.launches) == 1
    row = h.store.rows[response.sandbox_id]
    assert row.state is SandboxState.IN_USE
    assert row.container_id == f"helix-sb-{response.sandbox_id}"
    assert row.acquired_at is not None


@pytest.mark.asyncio
async def test_acquire_observes_cold_start_histogram() -> None:
    """Stream K.K10 — a cold acquire writes the cold-start histogram.

    Warm-session reuse (covered by ``test_acquire_reuses_warm_session_for_same_user``)
    must NOT observe; that test inspects ``cold_start=False`` on the
    response, which is the same gate the histogram uses (only the
    launch path executes).
    """
    from sandbox_supervisor.supervisor import _sandbox_cold_start_seconds

    before = _sandbox_cold_start_seconds._sum.get()  # type: ignore[attr-defined]
    h = _harness()
    await h.supervisor.acquire(_acquire_request())
    after = _sandbox_cold_start_seconds._sum.get()  # type: ignore[attr-defined]
    assert after > before


@pytest.mark.asyncio
async def test_acquire_emits_sandbox_acquired_audit() -> None:
    h = _harness()
    await h.supervisor.acquire(_acquire_request())

    assert len(h.audit.entries) == 1
    entry = h.audit.entries[0]
    assert entry.action is AuditAction.SANDBOX_ACQUIRED
    assert entry.result is AuditResult.SUCCESS


@pytest.mark.asyncio
async def test_release_removes_container_and_marks_destroyed() -> None:
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())
    await h.supervisor.release(response.sandbox_id)

    assert h.docker.removed == [f"helix-sb-{response.sandbox_id}"]
    assert h.docker.links[0].closed is True
    row = h.store.rows[response.sandbox_id]
    assert row.state is SandboxState.DESTROYED
    assert row.destroy_reason == "release"
    assert row.released_at is not None


@pytest.mark.asyncio
async def test_release_does_not_emit_force_destroy_audit() -> None:
    # A routine release is not a force-destroy — only the acquire audit fires.
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())
    await h.supervisor.release(response.sandbox_id)

    actions = [e.action for e in h.audit.entries]
    assert actions == [AuditAction.SANDBOX_ACQUIRED]


@pytest.mark.asyncio
async def test_acquire_launch_failure_marks_failed_and_raises() -> None:
    h = _harness(docker=RecordingDockerClient(launch_error=DockerError("daemon down")))
    with pytest.raises(SupervisorError, match="sandbox launch failed"):
        await h.supervisor.acquire(_acquire_request())

    states = [r.state for r in h.store.rows.values()]
    assert states == [SandboxState.FAILED]


@pytest.mark.asyncio
async def test_acquire_runner_not_ready_marks_failed_and_raises() -> None:
    # The container launches but the runner never reports ready.
    h = _harness(docker=RecordingDockerClient(link=FakeRunnerLink(ready=False)))
    with pytest.raises(SupervisorError, match="sandbox launch failed"):
        await h.supervisor.acquire(_acquire_request())

    states = [r.state for r in h.store.rows.values()]
    assert states == [SandboxState.FAILED]


@pytest.mark.asyncio
async def test_destroy_is_idempotent() -> None:
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())
    await h.supervisor.destroy(response.sandbox_id, reason="cancelled")
    # A second destroy is a no-op — no extra docker.remove call.
    await h.supervisor.destroy(response.sandbox_id, reason="cancelled")

    assert h.docker.removed == [f"helix-sb-{response.sandbox_id}"]


@pytest.mark.asyncio
async def test_destroy_unknown_sandbox_raises_not_found() -> None:
    h = _harness()
    with pytest.raises(SandboxNotFoundError):
        await h.supervisor.destroy(uuid4(), reason="cancelled")


@pytest.mark.asyncio
async def test_forced_destroy_emits_force_destroy_audit() -> None:
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())
    await h.supervisor.destroy(response.sandbox_id, reason="cancelled")

    actions = [e.action for e in h.audit.entries]
    assert AuditAction.SANDBOX_FORCE_DESTROY in actions


# ---------------------------------------------------------------------------
# J.15 — the per-user persistent workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_without_user_uses_ephemeral_tmpfs() -> None:
    # No user_id → the pre-J.15 ephemeral-tmpfs path.
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())

    argv = h.docker.launches[0]
    assert "--tmpfs" in argv
    assert "--volume" not in argv
    row = h.store.rows[response.sandbox_id]
    assert row.user_id is None
    assert row.workspace_id is None


@pytest.mark.asyncio
async def test_acquire_with_user_mounts_persistent_volume() -> None:
    h = _harness()
    tenant, user = uuid4(), uuid4()
    response = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))

    argv = h.docker.launches[0]
    assert f"{workspace_volume_name(tenant, user)}:/workspace" in argv
    assert "--tmpfs" not in argv

    row = h.store.rows[response.sandbox_id]
    assert row.user_id == user
    assert row.workspace_id is not None


@pytest.mark.asyncio
async def test_acquire_reuses_one_workspace_per_user() -> None:
    # Two acquires for the same (tenant, user) resolve the same workspace.
    h = _harness()
    tenant, user = uuid4(), uuid4()
    r1 = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))
    r2 = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))

    workspace_id = h.store.rows[r1.sandbox_id].workspace_id
    assert workspace_id is not None
    assert h.store.rows[r2.sandbox_id].workspace_id == workspace_id


@pytest.mark.asyncio
async def test_acquire_audit_flags_persistent_workspace() -> None:
    h = _harness()
    await h.supervisor.acquire(_acquire_request(user_id=uuid4()))
    assert h.audit.entries[0].details["persistent_workspace"] is True

    await h.supervisor.acquire(_acquire_request())
    assert h.audit.entries[1].details["persistent_workspace"] is False


# ---------------------------------------------------------------------------
# J.15 — warm per-user sandbox sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_reuses_warm_session_for_same_user() -> None:
    h = _harness()
    tenant, user = uuid4(), uuid4()

    first = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))
    second = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))

    # Same warm session — no second docker run, and the reuse is not cold.
    assert second.sandbox_id == first.sandbox_id
    assert second.cold_start is False
    assert len(h.docker.launches) == 1


@pytest.mark.asyncio
async def test_acquire_without_user_never_reuses() -> None:
    h = _harness()
    first = await h.supervisor.acquire(_acquire_request())
    second = await h.supervisor.acquire(_acquire_request())

    assert second.sandbox_id != first.sandbox_id
    assert len(h.docker.launches) == 2


@pytest.mark.asyncio
async def test_release_keeps_warm_session_alive() -> None:
    h = _harness()
    tenant, user = uuid4(), uuid4()
    acquired = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))

    await h.supervisor.release(acquired.sandbox_id)

    # A warm session is not torn down on release — it stays IN_USE...
    assert h.docker.removed == []
    assert h.store.rows[acquired.sandbox_id].state is SandboxState.IN_USE
    # ...and the user's next acquire reuses it.
    again = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))
    assert again.sandbox_id == acquired.sandbox_id
    assert len(h.docker.launches) == 1


@pytest.mark.asyncio
async def test_exec_stamps_last_used_at() -> None:
    h = _harness()
    acquired = await h.supervisor.acquire(_acquire_request(user_id=uuid4()))
    assert h.store.rows[acquired.sandbox_id].last_used_at is None

    await h.supervisor.exec(acquired.sandbox_id, code="print(1)")
    assert h.store.rows[acquired.sandbox_id].last_used_at is not None


@pytest.mark.asyncio
async def test_destroy_clears_warm_session() -> None:
    h = _harness()
    tenant, user = uuid4(), uuid4()
    first = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))
    await h.supervisor.destroy(first.sandbox_id, reason="cancelled")

    # The session entry is gone — the next acquire is a fresh cold start.
    second = await h.supervisor.acquire(_acquire_request(tenant, user_id=user))
    assert second.sandbox_id != first.sandbox_id
    assert second.cold_start is True
    assert len(h.docker.launches) == 2


@pytest.mark.asyncio
async def test_reaper_reaps_idle_warm_session() -> None:
    h = _harness()
    acquired = await h.supervisor.acquire(_acquire_request(user_id=uuid4()))
    # Backdate last_used_at past the idle TTL.
    record = h.store.rows[acquired.sandbox_id]
    h.store.rows[acquired.sandbox_id] = replace(
        record, last_used_at=datetime.now(UTC) - timedelta(hours=1)
    )

    reaper = SandboxReaper(supervisor=h.supervisor, store=h.store, interval_s=10.0, idle_ttl_s=900)
    reaped = await reaper.run_once()

    assert reaped == 1
    assert h.store.rows[acquired.sandbox_id].state is SandboxState.DESTROYED
    assert h.store.rows[acquired.sandbox_id].destroy_reason == DESTROY_REASON_IDLE_TIMEOUT


async def test_reaper_force_reaps_non_idle_session() -> None:
    # Stream P (Mini-ADR P-14) — idle_ttl_s=0 treats every active session as
    # idle, so a freshly-acquired (non-idle) session is reaped.
    h = _harness()
    acquired = await h.supervisor.acquire(_acquire_request(user_id=uuid4()))
    reaper = SandboxReaper(supervisor=h.supervisor, store=h.store, interval_s=10.0, idle_ttl_s=900)

    assert await reaper.run_once(idle_ttl_s=0) == 1
    assert h.store.rows[acquired.sandbox_id].state is SandboxState.DESTROYED


def test_reap_route_without_reaper_returns_zero() -> None:
    h = _harness()
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.post("/v1/sandboxes:reap", json={"force": True})
    assert resp.status_code == 200
    assert resp.json()["reaped_count"] == 0


# ---------------------------------------------------------------------------
# J.15-补强-1 — volume quota + lifecycle (Mini-ADR J-29 第 1 项 + J-36)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_rejects_when_workspace_size_at_limit() -> None:
    """Acquire raises WorkspaceQuotaExceededError when size_bytes >= size_limit_bytes."""
    h = _harness(quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    workspace = await h.workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
    # Lower the limit + push size above it to force the reject.
    await h.workspaces.update_size(workspace_id=workspace.id, size_bytes=2048)
    # InMemory model_copy idiom — modify the row's size_limit_bytes.
    h.workspaces._rows[(tenant_id, user_id)] = h.workspaces._rows[(tenant_id, user_id)].model_copy(
        update={"size_limit_bytes": 1024}
    )

    with pytest.raises(WorkspaceQuotaExceededError):
        await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    # Audit captured the deny.
    actions = [e.action for e in h.audit.entries]
    assert AuditAction.WORKSPACE_QUOTA_DENIED in actions
    # And no docker launch happened.
    assert h.docker.launches == []


@pytest.mark.asyncio
async def test_acquire_rejects_when_workspace_is_soft_deleted() -> None:
    """Acquire raises WorkspaceDeletedError when deleted_at is set."""
    h = _harness(quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    workspace = await h.workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
    await h.workspaces.soft_delete(workspace_id=workspace.id, now=datetime.now(UTC))

    with pytest.raises(WorkspaceDeletedError):
        await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))


@pytest.mark.asyncio
async def test_acquire_succeeds_under_quota_with_enforcer() -> None:
    """A healthy under-quota acquire still works when the enforcer is wired."""
    h = _harness(quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    response = await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    assert response.cold_start is True
    # No quota-denied audit; only the SANDBOX_ACQUIRED success.
    quota_denies = [e for e in h.audit.entries if e.action is AuditAction.WORKSPACE_QUOTA_DENIED]
    assert quota_denies == []


@pytest.mark.asyncio
async def test_reuse_session_rechecks_quota() -> None:
    """A warm-session reuse re-runs the workspace quota check (size can grow between
    acquires; soft-delete can land while a session is warm)."""
    h = _harness(quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    # Volume "grew" past quota between runs — push size + tighten limit.
    workspace = await h.workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
    await h.workspaces.update_size(workspace_id=workspace.id, size_bytes=4096)
    h.workspaces._rows[(tenant_id, user_id)] = h.workspaces._rows[(tenant_id, user_id)].model_copy(
        update={"size_limit_bytes": 1024}
    )

    with pytest.raises(WorkspaceQuotaExceededError):
        await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))


@pytest.mark.asyncio
async def test_release_fires_size_refresh_and_keeps_session_warm() -> None:
    """Releasing a user-scoped acquire keeps the warm session AND schedules a du.

    The QuotaEnforcer.refresh_size call is fire-and-forget — we let the
    event loop run pending tasks via ``asyncio.sleep(0)`` so the fake
    docker's ``measure_calls`` and the store's ``size_bytes`` are
    populated before we assert.
    """
    import asyncio as _asyncio  # local import keeps the test scope explicit

    docker = RecordingDockerClient(measured_size=12345)
    h = _harness(docker=docker, quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    response = await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    await h.supervisor.release(response.sandbox_id)

    # Yield once so the create_task'd refresh runs to completion.
    for _ in range(5):
        await _asyncio.sleep(0)

    # Warm session stayed alive.
    row = h.store.rows[response.sandbox_id]
    assert row.state is SandboxState.IN_USE
    # And du was scheduled + written back.
    assert len(docker.measure_calls) == 1
    refreshed = await h.workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
    assert refreshed.size_bytes == 12345


@pytest.mark.asyncio
async def test_mark_workspace_deleted_soft_deletes_and_destroys_warm_session() -> None:
    """mark_workspace_deleted soft-deletes the row + force-destroys any warm session."""
    h = _harness(quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    await h.supervisor.mark_workspace_deleted(tenant_id=tenant_id, user_id=user_id)

    workspace = await h.workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
    assert workspace.deleted_at is not None

    # Warm session was force-destroyed; subsequent acquire rejects.
    with pytest.raises(WorkspaceDeletedError):
        await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    # Audit captured the soft-delete + the implicit force-destroy.
    actions = [e.action for e in h.audit.entries]
    assert AuditAction.WORKSPACE_SOFT_DELETE in actions
    assert AuditAction.SANDBOX_FORCE_DESTROY in actions

    # The workspace audit entry uses resource_type="user_workspace".
    workspace_entries = [
        e for e in h.audit.entries if e.action is AuditAction.WORKSPACE_SOFT_DELETE
    ]
    assert len(workspace_entries) == 1
    assert workspace_entries[0].resource_type == "user_workspace"
    assert workspace_entries[0].resource_id == str(workspace.id)


@pytest.mark.asyncio
async def test_mark_workspace_deleted_is_idempotent() -> None:
    """A second mark_workspace_deleted is a no-op (no extra audit, no extra destroy)."""
    h = _harness(quota_enabled=True)
    tenant_id, user_id = uuid4(), uuid4()
    await h.supervisor.acquire(_acquire_request(tenant_id, user_id=user_id))

    await h.supervisor.mark_workspace_deleted(tenant_id=tenant_id, user_id=user_id)
    audits_after_first = len(h.audit.entries)

    await h.supervisor.mark_workspace_deleted(tenant_id=tenant_id, user_id=user_id)
    # Second call adds no new audit entries.
    assert len(h.audit.entries) == audits_after_first


# ---------------------------------------------------------------------------
# J.9 — workspace file read (artifact content download)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_workspace_file_returns_content() -> None:
    h = _harness(docker=RecordingDockerClient(volume_file=b"report body"))
    data = await h.supervisor.read_workspace_file(
        tenant_id=uuid4(), user_id=uuid4(), path="report.md"
    )
    assert data == b"report body"
    # The volume name is the deterministic per-(tenant, user) identifier.
    assert h.docker.volume_reads[0][1] == "report.md"


@pytest.mark.asyncio
async def test_read_workspace_file_rejects_unsafe_path() -> None:
    h = _harness()
    for bad in ("/etc/passwd", "../escape"):
        with pytest.raises(WorkspaceFileNotFoundError):
            await h.supervisor.read_workspace_file(tenant_id=uuid4(), user_id=uuid4(), path=bad)
    # A rejected path never reaches docker.
    assert h.docker.volume_reads == []


@pytest.mark.asyncio
async def test_read_workspace_file_missing_maps_to_not_found() -> None:
    h = _harness(docker=RecordingDockerClient(volume_file_error=DockerError("no such file")))
    with pytest.raises(WorkspaceFileNotFoundError):
        await h.supervisor.read_workspace_file(tenant_id=uuid4(), user_id=uuid4(), path="gone.md")


@pytest.mark.asyncio
async def test_read_workspace_file_too_large_raises() -> None:
    oversize = b"\0" * (_MAX_ARTIFACT_BYTES + 1)
    h = _harness(docker=RecordingDockerClient(volume_file=oversize))
    with pytest.raises(WorkspaceFileTooLargeError):
        await h.supervisor.read_workspace_file(tenant_id=uuid4(), user_id=uuid4(), path="big.bin")


def test_read_workspace_file_route_returns_content() -> None:
    h = _harness(docker=RecordingDockerClient(volume_file=b"hi"))
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.get(f"/v1/workspaces/{uuid4()}/{uuid4()}/file", params={"path": "x.txt"})
    assert resp.status_code == 200
    assert resp.content == b"hi"


def test_read_workspace_file_route_404_on_missing() -> None:
    h = _harness(docker=RecordingDockerClient(volume_file_error=DockerError("no such file")))
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.get(f"/v1/workspaces/{uuid4()}/{uuid4()}/file", params={"path": "gone"})
    assert resp.status_code == 404


def test_metrics_route_exposes_prometheus_text() -> None:
    # Stream P (Mini-ADR P-15) — the supervisor is a standalone scrape target;
    # /metrics must expose its in-process registry (cold-start histogram et al.)
    # so Prometheus can collect the Phase 6 sandbox SLO.
    h = _harness(docker=RecordingDockerClient(volume_file=b"hi"))
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    # The cold-start histogram is module-level in supervisor.py, registered at
    # import, so it appears in the exposition even before any observation.
    assert "helix_sandbox_cold_start_seconds" in resp.text


# ---------------------------------------------------------------------------
# F.4a — the held-pipe exec channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_runs_code_via_the_runner_link() -> None:
    link = FakeRunnerLink(
        exec_result=ExecResult(stdout="42\n", stderr="", exit_code=0, timed_out=False)
    )
    h = _harness(docker=RecordingDockerClient(link=link))
    response = await h.supervisor.acquire(_acquire_request())

    result = await h.supervisor.exec(response.sandbox_id, code="print(42)", timeout_s=10)
    assert result.stdout == "42\n"
    assert result.exit_code == 0
    assert link.exec_calls == [("print(42)", 10)]


@pytest.mark.asyncio
async def test_exec_defaults_timeout_to_service_default() -> None:
    link = FakeRunnerLink()
    h = _harness(
        docker=RecordingDockerClient(link=link),
        settings=SandboxSupervisorSettings(default_timeout_s=25),
    )
    response = await h.supervisor.acquire(_acquire_request())

    await h.supervisor.exec(response.sandbox_id, code="print(1)")
    assert link.exec_calls == [("print(1)", 25)]


@pytest.mark.asyncio
async def test_exec_unknown_sandbox_raises_not_found() -> None:
    h = _harness()
    with pytest.raises(SandboxNotFoundError):
        await h.supervisor.exec(uuid4(), code="print(1)")


@pytest.mark.asyncio
async def test_exec_link_failure_raises_supervisor_error() -> None:
    link = FakeRunnerLink(exec_error=RunnerLinkError("runner closed the connection"))
    h = _harness(docker=RecordingDockerClient(link=link))
    response = await h.supervisor.acquire(_acquire_request())

    with pytest.raises(SupervisorError, match="sandbox exec failed"):
        await h.supervisor.exec(response.sandbox_id, code="print(1)")


@pytest.mark.asyncio
async def test_exec_unavailable_after_release() -> None:
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())
    await h.supervisor.release(response.sandbox_id)
    # The link was dropped on release — exec can no longer reach it.
    with pytest.raises(SandboxNotFoundError):
        await h.supervisor.exec(response.sandbox_id, code="print(1)")


# ---------------------------------------------------------------------------
# #41 — quota denial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_denied_when_tenant_at_quota() -> None:
    tenant = uuid4()
    store = InMemorySandboxStore(limit=2)
    store.seed_active(tenant)
    store.seed_active(tenant)
    h = _harness(store=store)

    with pytest.raises(QuotaExceededError) as excinfo:
        await h.supervisor.acquire(_acquire_request(tenant))
    assert excinfo.value.limit == 2
    # The container was never launched.
    assert h.docker.launches == []


@pytest.mark.asyncio
async def test_quota_denial_emits_audit() -> None:
    tenant = uuid4()
    store = InMemorySandboxStore(limit=1)
    store.seed_active(tenant)
    h = _harness(store=store)

    with pytest.raises(QuotaExceededError):
        await h.supervisor.acquire(_acquire_request(tenant))

    assert len(h.audit.entries) == 1
    entry = h.audit.entries[0]
    assert entry.action is AuditAction.SANDBOX_QUOTA_DENIED
    assert entry.result is AuditResult.DENIED
    assert entry.reason is not None


@pytest.mark.asyncio
async def test_acquire_falls_back_to_default_quota_without_a_row() -> None:
    # No tenant_quota row (limit=None) → the settings default applies.
    tenant = uuid4()
    store = InMemorySandboxStore(limit=None)
    store.seed_active(tenant)
    h = _harness(store=store, settings=SandboxSupervisorSettings(default_max_sandboxes=1))

    with pytest.raises(QuotaExceededError) as excinfo:
        await h.supervisor.acquire(_acquire_request(tenant))
    assert excinfo.value.limit == 1


@pytest.mark.asyncio
async def test_acquire_allowed_below_quota() -> None:
    tenant = uuid4()
    store = InMemorySandboxStore(limit=5)
    store.seed_active(tenant)
    h = _harness(store=store)

    response = await h.supervisor.acquire(_acquire_request(tenant))
    assert response.sandbox_id in store.rows


# ---------------------------------------------------------------------------
# #42 — TTL reaper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_destroys_orphaned_sandbox() -> None:
    h = _harness()
    orphan = h.store.seed_active(uuid4())
    # Backdate acquired_at well past the idle TTL (900s).
    stale = replace(orphan, acquired_at=datetime.now(UTC) - timedelta(hours=1))
    h.store.rows[orphan.id] = stale

    reaper = SandboxReaper(supervisor=h.supervisor, store=h.store, interval_s=10.0, idle_ttl_s=900)
    reaped = await reaper.run_once()

    assert reaped == 1
    assert h.store.rows[orphan.id].state is SandboxState.DESTROYED
    assert h.store.rows[orphan.id].destroy_reason == DESTROY_REASON_IDLE_TIMEOUT
    assert f"helix-sb-{orphan.id}" in h.docker.removed


@pytest.mark.asyncio
async def test_reaper_leaves_fresh_sandbox_alone() -> None:
    h = _harness()
    fresh = h.store.seed_active(uuid4())  # acquired_at = now

    reaper = SandboxReaper(supervisor=h.supervisor, store=h.store, interval_s=10.0, idle_ttl_s=900)
    reaped = await reaper.run_once()

    assert reaped == 0
    assert h.store.rows[fresh.id].state is SandboxState.IN_USE


# ---------------------------------------------------------------------------
# HTTP route smoke tests
# ---------------------------------------------------------------------------


def test_acquire_route_returns_response() -> None:
    h = _harness()
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/sandboxes:acquire",
            json={"tenant_id": str(uuid4()), "thread_id": "t-1"},
        )
    assert resp.status_code == 200
    assert resp.json()["container_id"].startswith("helix-sb-")


def test_exec_route_returns_runner_output() -> None:
    link = FakeRunnerLink(
        exec_result=ExecResult(stdout="hi\n", stderr="", exit_code=0, timed_out=False)
    )
    h = _harness(docker=RecordingDockerClient(link=link))
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        acquired = client.post(
            "/v1/sandboxes:acquire",
            json={"tenant_id": str(uuid4()), "thread_id": "t-1"},
        ).json()
        resp = client.post(
            f"/v1/sandboxes/{acquired['sandbox_id']}:exec",
            json={"code": "print('hi')"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stdout"] == "hi\n"
    assert body["exit_code"] == 0


def test_release_route_returns_204() -> None:
    h = _harness()
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        acquired = client.post(
            "/v1/sandboxes:acquire",
            json={"tenant_id": str(uuid4()), "thread_id": "t-1"},
        ).json()
        resp = client.post(f"/v1/sandboxes/{acquired['sandbox_id']}:release")
    assert resp.status_code == 204


def test_destroy_route_returns_204() -> None:
    # The forced-teardown route the F.7 cancellation path calls.
    h = _harness()
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        acquired = client.post(
            "/v1/sandboxes:acquire",
            json={"tenant_id": str(uuid4()), "thread_id": "t-1"},
        ).json()
        resp = client.post(
            f"/v1/sandboxes/{acquired['sandbox_id']}:destroy",
            json={"reason": "cancelled"},
        )
    assert resp.status_code == 204


def test_acquire_route_returns_429_when_at_quota() -> None:
    tenant = uuid4()
    store = InMemorySandboxStore(limit=1)
    store.seed_active(tenant)
    h = _harness(store=store)
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/sandboxes:acquire",
            json={"tenant_id": str(tenant), "thread_id": "t-1"},
        )
    assert resp.status_code == 429


def test_health_route_reports_docker_status() -> None:
    h = _harness()
    app = create_app(SandboxSupervisorSettings(), supervisor=h.supervisor, enable_reaper=False)
    with TestClient(app) as client:
        resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["docker_ok"] is True
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# Stream OFFICE-1a — image variant selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_office_variant_selects_office_image() -> None:
    settings = SandboxSupervisorSettings(sandbox_image_office="helix-sandbox-office:test")
    h = _harness(settings=settings)
    resp = await h.supervisor.acquire(_acquire_request(image_variant="office"))
    assert h.store.rows[resp.sandbox_id].image_ref == "helix-sandbox-office:test"


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", [None, "minimal", "bogus"])
async def test_acquire_default_variant_selects_minimal_image(variant: str | None) -> None:
    settings = SandboxSupervisorSettings(sandbox_image="helix-sandbox:test")
    h = _harness(settings=settings)
    resp = await h.supervisor.acquire(_acquire_request(image_variant=variant))
    assert h.store.rows[resp.sandbox_id].image_ref == "helix-sandbox:test"


@pytest.mark.asyncio
async def test_warm_session_not_reused_across_image_variants() -> None:
    # A minimal warm session must not be reused for an office acquire — the
    # office agent needs the office-libs image (Stream OFFICE-1a).
    user = uuid4()
    tenant = uuid4()
    h = _harness()
    r1 = await h.supervisor.acquire(_acquire_request(tenant, user_id=user, image_variant=None))
    assert r1.cold_start is True
    r2 = await h.supervisor.acquire(_acquire_request(tenant, user_id=user, image_variant="office"))
    assert r2.cold_start is True  # NOT reused
    assert r2.sandbox_id != r1.sandbox_id
    assert h.store.rows[r2.sandbox_id].image_ref == h.supervisor._settings.sandbox_image_office
    # The stale-variant session is torn down now (not left for the reaper),
    # so it stops counting toward the tenant sandbox quota (review MEDIUM).
    assert h.store.rows[r1.sandbox_id].state is SandboxState.DESTROYED
