"""``SandboxSupervisor`` — the F.1 sandbox lifecycle core.

M0 cold-start (Mini-ADR F-4): ``acquire`` is a fresh ``docker run``,
``release`` / ``destroy`` a ``docker rm -f``. No warm pool. All
dependencies are injected so the logic is unit-testable with fakes
(test matrix #40 / #41 / #42).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from helix_agent.protocol import AuditEntry
from helix_agent.protocol.audit import AuditAction, AuditResult
from helix_agent.runtime.sandbox import SandboxResourceLimits, SandboxRuntimeProvider
from sandbox_supervisor.docker_client import DockerClient, DockerError
from sandbox_supervisor.domain import (
    DESTROY_REASON_RELEASE,
    QuotaExceededError,
    SandboxNotFoundError,
    SandboxRecord,
    SandboxState,
    SupervisorError,
)
from sandbox_supervisor.schemas import AcquireRequest, AcquireResponse
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.store import SandboxStore

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    """The audit surface the supervisor needs — :class:`AuditLogger` satisfies it."""

    async def write(self, entry: AuditEntry) -> None:
        """Persist one audit entry."""


class SandboxSupervisor:
    """Owns the ``acquire`` / ``release`` / ``destroy`` lifecycle."""

    def __init__(
        self,
        *,
        store: SandboxStore,
        docker: DockerClient,
        audit: AuditSink,
        runtime_provider: SandboxRuntimeProvider,
        settings: SandboxSupervisorSettings,
    ) -> None:
        self._store = store
        self._docker = docker
        self._audit = audit
        self._runtime = runtime_provider
        self._settings = settings

    async def acquire(self, request: AcquireRequest) -> AcquireResponse:
        """Quota-check, then launch a fresh sandbox container.

        Raises :class:`QuotaExceededError` when the tenant is at its
        cap, and :class:`SupervisorError` when the container fails to
        launch (the row is left ``FAILED`` for observability).
        """
        await self._enforce_quota(request.tenant_id)

        record = self._new_record(request)
        await self._store.insert(record)

        try:
            container_id = await self._docker.run(self._run_argv(record))
        except DockerError as exc:
            await self._store.update(record.with_state(SandboxState.FAILED))
            msg = f"sandbox launch failed: {exc}"
            raise SupervisorError(msg) from exc

        acquired_at = datetime.now(UTC)
        running = record.with_state(
            SandboxState.IN_USE,
            container_id=container_id,
            acquired_at=acquired_at,
        )
        await self._store.update(running)
        await self._emit_audit(
            tenant_id=record.tenant_id,
            action=AuditAction.SANDBOX_ACQUIRED,
            result=AuditResult.SUCCESS,
            sandbox_id=record.id,
            details={"image_ref": record.image_ref, "thread_id": record.thread_id},
        )
        return AcquireResponse(
            sandbox_id=record.id,
            container_id=container_id,
            cold_start=True,
            acquired_at=acquired_at,
        )

    async def release(self, sandbox_id: UUID) -> None:
        """Routine teardown — no force-destroy audit."""
        await self.destroy(sandbox_id, reason=DESTROY_REASON_RELEASE)

    async def destroy(self, sandbox_id: UUID, *, reason: str) -> None:
        """Tear a sandbox down — ``docker rm -f`` + mark ``DESTROYED``.

        Idempotent: destroying an already-terminal sandbox is a no-op.
        A non-``release`` reason emits a ``sandbox:force_destroy`` audit.
        """
        record = await self._store.get(sandbox_id)
        if record is None:
            raise SandboxNotFoundError(sandbox_id)
        if record.state in (SandboxState.DESTROYED, SandboxState.FAILED):
            return

        if record.container_id is not None:
            await self._docker.remove(record.container_id)

        now = datetime.now(UTC)
        released_at = now if reason == DESTROY_REASON_RELEASE else record.released_at
        await self._store.update(
            record.with_state(
                SandboxState.DESTROYED,
                destroyed_at=now,
                destroy_reason=reason,
                released_at=released_at,
            )
        )
        if reason != DESTROY_REASON_RELEASE:
            await self._emit_audit(
                tenant_id=record.tenant_id,
                action=AuditAction.SANDBOX_FORCE_DESTROY,
                result=AuditResult.SUCCESS,
                sandbox_id=sandbox_id,
                details={"reason": reason},
            )

    async def docker_ok(self) -> bool:
        """Whether the Docker daemon is reachable — for the health probe."""
        return await self._docker.ping()

    # ------------------------------------------------------------------

    async def _enforce_quota(self, tenant_id: UUID) -> None:
        limit = await self._store.sandbox_limit_for_tenant(tenant_id)
        if limit is None:
            limit = self._settings.default_max_sandboxes
        active = await self._store.count_active_for_tenant(tenant_id)
        if active >= limit:
            await self._emit_audit(
                tenant_id=tenant_id,
                action=AuditAction.SANDBOX_QUOTA_DENIED,
                result=AuditResult.DENIED,
                sandbox_id=None,
                details={"active": active, "limit": limit},
                reason=f"tenant at sandbox quota ({active}/{limit})",
            )
            raise QuotaExceededError(tenant_id, limit)

    def _new_record(self, request: AcquireRequest) -> SandboxRecord:
        s = self._settings
        return SandboxRecord(
            id=uuid4(),
            tenant_id=request.tenant_id,
            image_ref=s.sandbox_image,
            node=s.node_name,
            container_id=None,
            state=SandboxState.CREATING,
            thread_id=request.thread_id,
            cpu_quota=request.cpu if request.cpu is not None else s.default_cpu,
            memory_mb=request.memory_mb if request.memory_mb is not None else s.default_memory_mb,
            pids_limit=(
                request.pids_limit if request.pids_limit is not None else s.default_pids_limit
            ),
            timeout_s=request.timeout_s if request.timeout_s is not None else s.default_timeout_s,
            created_at=datetime.now(UTC),
        )

    def _run_argv(self, record: SandboxRecord) -> list[str]:
        """Compose the ``docker run`` argv: hardening flags from the F.3
        provider, plus ``--detach`` so the container outlives this call."""
        argv = self._runtime.docker_run_argv(
            image=record.image_ref,
            container_name=f"helix-sb-{record.id}",
            limits=SandboxResourceLimits(
                cpus=record.cpu_quota,
                memory_mb=record.memory_mb,
                pids_limit=record.pids_limit,
            ),
        )
        # Insert after ["docker", "run", ...] so the supervisor gets a
        # container id back and the runner stays alive on stdin.
        argv.insert(2, "--detach")
        return argv

    async def _emit_audit(
        self,
        *,
        tenant_id: UUID,
        action: AuditAction,
        result: AuditResult,
        sandbox_id: UUID | None,
        details: dict[str, object],
        reason: str | None = None,
    ) -> None:
        await self._audit.write(
            AuditEntry(
                tenant_id=tenant_id,
                actor_type="system",
                actor_id=self._settings.service_name,
                action=action,
                resource_type="sandbox",
                resource_id=str(sandbox_id) if sandbox_id is not None else None,
                result=result,
                reason=reason,
                details=details,
            )
        )
