"""Unit tests for the Sandbox Supervisor — Stream F.1 (test matrix #40/#41/#42).

All Docker / DB dependencies are faked, so these run in the plain
``pytest`` job — no testcontainers. Three groups:

* #40 — acquire / release lifecycle + state machine
* #41 — per-tenant quota denial + audit
* #42 — the TTL reaper

Plus a few HTTP-route smoke tests over an injected supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

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
)
from sandbox_supervisor.reaper import SandboxReaper
from sandbox_supervisor.schemas import AcquireRequest
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.supervisor import SandboxSupervisor

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class RecordingDockerClient:
    """A :class:`DockerClient` that records calls and never touches Docker."""

    def __init__(self, *, run_error: DockerError | None = None) -> None:
        self.runs: list[list[str]] = []
        self.removed: list[str] = []
        self._run_error = run_error
        self._counter = 0

    async def run(self, argv: list[str]) -> str:
        self.runs.append(argv)
        if self._run_error is not None:
            raise self._run_error
        self._counter += 1
        return f"container-{self._counter}"

    async def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    async def ping(self) -> bool:
        return True


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

    async def list_orphans(self, *, now: datetime, grace_s: int) -> list[SandboxRecord]:
        return [
            r
            for r in self.rows.values()
            if r.state == SandboxState.IN_USE
            and r.acquired_at is not None
            and r.acquired_at + timedelta(seconds=r.timeout_s + grace_s) < now
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


def _harness(
    *,
    store: InMemorySandboxStore | None = None,
    docker: RecordingDockerClient | None = None,
    settings: SandboxSupervisorSettings | None = None,
) -> _Harness:
    resolved_store = store if store is not None else InMemorySandboxStore()
    resolved_docker = docker if docker is not None else RecordingDockerClient()
    audit = RecordingAuditSink()
    supervisor = SandboxSupervisor(
        store=resolved_store,
        docker=resolved_docker,
        audit=audit,
        runtime_provider=SandboxRuntimeProvider(oci_runtime="runc"),
        settings=settings or SandboxSupervisorSettings(),
    )
    return _Harness(supervisor, resolved_store, resolved_docker, audit)


def _running_record(tenant_id: UUID, *, acquired_at: datetime) -> SandboxRecord:
    sandbox_id = uuid4()
    return SandboxRecord(
        id=sandbox_id,
        tenant_id=tenant_id,
        image_ref="helix-sandbox:dev",
        node="local",
        container_id=f"container-{sandbox_id}",
        state=SandboxState.IN_USE,
        thread_id="t-1",
        cpu_quota=1.0,
        memory_mb=512,
        pids_limit=128,
        timeout_s=30,
        created_at=acquired_at,
        acquired_at=acquired_at,
    )


def _acquire_request(tenant_id: UUID | None = None) -> AcquireRequest:
    return AcquireRequest(tenant_id=tenant_id or uuid4(), thread_id="t-1")


# ---------------------------------------------------------------------------
# #40 — acquire / release lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_runs_container_and_marks_in_use() -> None:
    h = _harness()
    response = await h.supervisor.acquire(_acquire_request())

    assert response.container_id == "container-1"
    assert response.cold_start is True
    assert len(h.docker.runs) == 1
    row = h.store.rows[response.sandbox_id]
    assert row.state is SandboxState.IN_USE
    assert row.container_id == "container-1"
    assert row.acquired_at is not None


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

    assert h.docker.removed == ["container-1"]
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
async def test_acquire_docker_failure_marks_failed_and_raises() -> None:
    h = _harness(docker=RecordingDockerClient(run_error=DockerError("daemon down")))
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

    assert h.docker.removed == ["container-1"]


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
    assert h.docker.runs == []


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
    # Backdate acquired_at well past timeout_s (30) + grace (30).
    stale = replace(orphan, acquired_at=datetime.now(UTC) - timedelta(hours=1))
    h.store.rows[orphan.id] = stale

    reaper = SandboxReaper(supervisor=h.supervisor, store=h.store, interval_s=10.0, grace_s=30)
    reaped = await reaper.run_once()

    assert reaped == 1
    assert h.store.rows[orphan.id].state is SandboxState.DESTROYED
    assert h.store.rows[orphan.id].destroy_reason == DESTROY_REASON_IDLE_TIMEOUT
    assert orphan.container_id in h.docker.removed


@pytest.mark.asyncio
async def test_reaper_leaves_fresh_sandbox_alone() -> None:
    h = _harness()
    fresh = h.store.seed_active(uuid4())  # acquired_at = now

    reaper = SandboxReaper(supervisor=h.supervisor, store=h.store, interval_s=10.0, grace_s=30)
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
    assert resp.json()["container_id"] == "container-1"


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
