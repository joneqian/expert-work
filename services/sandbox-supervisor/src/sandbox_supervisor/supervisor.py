"""``SandboxSupervisor`` — the F.1 sandbox lifecycle core.

M0 cold-start (Mini-ADR F-4): ``acquire`` is a fresh ``docker run``,
``release`` / ``destroy`` a ``docker rm -f``. No warm pool.

Transport is the held-pipe (option C): ``acquire`` launches the
container with ``docker run -i`` and keeps the subprocess; the
supervisor holds a :class:`RunnerLink` per sandbox and ``exec`` drives
the runner protocol over it. All dependencies are injected so the logic
is unit-testable with fakes (test matrix #40 / #41 / #42 + exec).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from helix_agent.persistence import UserWorkspaceStore
from helix_agent.protocol import AuditEntry, UserWorkspace
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
from sandbox_supervisor.runner_link import ExecResult, RunnerLink, RunnerLinkError
from sandbox_supervisor.schemas import AcquireRequest, AcquireResponse
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.store import SandboxStore

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    """The audit surface the supervisor needs — :class:`AuditLogger` satisfies it."""

    async def write(self, entry: AuditEntry) -> None:
        """Persist one audit entry."""


def _container_name(sandbox_id: UUID) -> str:
    """The deterministic ``--name`` for a sandbox's container."""
    return f"helix-sb-{sandbox_id}"


class SandboxSupervisor:
    """Owns the ``acquire`` / ``exec`` / ``release`` / ``destroy`` lifecycle."""

    def __init__(
        self,
        *,
        store: SandboxStore,
        docker: DockerClient,
        audit: AuditSink,
        runtime_provider: SandboxRuntimeProvider,
        workspace_store: UserWorkspaceStore,
        settings: SandboxSupervisorSettings,
    ) -> None:
        self._store = store
        self._docker = docker
        self._audit = audit
        self._runtime = runtime_provider
        self._workspaces = workspace_store
        self._settings = settings
        # Held runner links, keyed by sandbox id — the option-C transport.
        self._links: dict[UUID, RunnerLink] = {}

    async def acquire(self, request: AcquireRequest) -> AcquireResponse:
        """Quota-check, launch a fresh sandbox, wait for the runner to be ready.

        Raises :class:`QuotaExceededError` when the tenant is at its
        cap, and :class:`SupervisorError` when the container fails to
        launch or never reports ready (the row is left ``FAILED``).
        """
        await self._enforce_quota(request.tenant_id)

        # Stream J.15 — a user-scoped acquire mounts that user's
        # persistent workspace volume at /workspace; resolve (creating
        # on first use) the user_workspace row. No user_id → an
        # ephemeral tmpfs workspace (the pre-J.15 behaviour).
        workspace: UserWorkspace | None = None
        if request.user_id is not None:
            workspace = await self._workspaces.resolve(
                tenant_id=request.tenant_id, user_id=request.user_id
            )

        record = self._new_record(request, workspace=workspace)
        await self._store.insert(record)

        workspace_volume = workspace.volume_name if workspace is not None else None
        try:
            link = await self._docker.launch(
                self._run_argv(record, workspace_volume=workspace_volume)
            )
            await link.wait_ready(self._settings.runner_ready_timeout_s)
        except (DockerError, RunnerLinkError) as exc:
            await self._store.update(record.with_state(SandboxState.FAILED))
            msg = f"sandbox launch failed: {exc}"
            raise SupervisorError(msg) from exc

        self._links[record.id] = link
        acquired_at = datetime.now(UTC)
        await self._store.update(
            record.with_state(
                SandboxState.IN_USE,
                container_id=_container_name(record.id),
                acquired_at=acquired_at,
            )
        )
        await self._emit_audit(
            tenant_id=record.tenant_id,
            action=AuditAction.SANDBOX_ACQUIRED,
            result=AuditResult.SUCCESS,
            sandbox_id=record.id,
            details={
                "image_ref": record.image_ref,
                "thread_id": record.thread_id,
                "persistent_workspace": workspace is not None,
            },
        )
        return AcquireResponse(
            sandbox_id=record.id,
            container_id=_container_name(record.id),
            cold_start=True,
            acquired_at=acquired_at,
        )

    async def exec(
        self, sandbox_id: UUID, *, code: str, timeout_s: int | None = None
    ) -> ExecResult:
        """Run ``code`` in an acquired sandbox via its held runner link.

        ``timeout_s`` omitted → the service default. Raises
        :class:`SandboxNotFoundError` when no live sandbox holds that id,
        and :class:`SupervisorError` when the runner link fails.
        """
        link = self._links.get(sandbox_id)
        if link is None:
            raise SandboxNotFoundError(sandbox_id)
        resolved_timeout = timeout_s if timeout_s is not None else self._settings.default_timeout_s
        try:
            return await link.exec(code, resolved_timeout)
        except RunnerLinkError as exc:
            msg = f"sandbox exec failed: {exc}"
            raise SupervisorError(msg) from exc

    async def release(self, sandbox_id: UUID) -> None:
        """Routine teardown — no force-destroy audit."""
        await self.destroy(sandbox_id, reason=DESTROY_REASON_RELEASE)

    async def destroy(self, sandbox_id: UUID, *, reason: str) -> None:
        """Tear a sandbox down — ``docker rm -f`` + close the link + mark DESTROYED.

        Idempotent: destroying an already-terminal sandbox is a no-op.
        A non-``release`` reason emits a ``sandbox:force_destroy`` audit.

        A forced teardown (cancel / TTL reaper) SIGKILLs the container
        *before* closing the link: ``link.close()`` waits on a stdin-EOF
        that a busy runner only sees once its current ``exec`` returns,
        which would blow the gate-#8 ≤1s budget (Mini-ADR F-8). A routine
        ``release`` closes the pipe gracefully first, with ``docker rm``
        as the backstop.
        """
        record = await self._store.get(sandbox_id)
        if record is None:
            raise SandboxNotFoundError(sandbox_id)
        if record.state in (SandboxState.DESTROYED, SandboxState.FAILED):
            return

        link = self._links.pop(sandbox_id, None)
        forced = reason != DESTROY_REASON_RELEASE
        if forced:
            await self._docker.remove(_container_name(sandbox_id))
        if link is not None:
            await link.close()
        if not forced:
            await self._docker.remove(_container_name(sandbox_id))

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

    def _new_record(
        self, request: AcquireRequest, *, workspace: UserWorkspace | None
    ) -> SandboxRecord:
        s = self._settings
        return SandboxRecord(
            id=uuid4(),
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            workspace_id=workspace.id if workspace is not None else None,
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

    def _run_argv(self, record: SandboxRecord, *, workspace_volume: str | None) -> list[str]:
        """The hardened ``docker run`` argv from the F.3 provider.

        The provider's argv already carries ``--interactive`` — option C
        keeps the container attached so the supervisor holds its stdio;
        no ``--detach`` is added. ``workspace_volume`` selects the
        ``/workspace`` backing — a J.15 persistent volume or an
        ephemeral tmpfs (``None``).
        """
        return self._runtime.docker_run_argv(
            image=record.image_ref,
            container_name=_container_name(record.id),
            limits=SandboxResourceLimits(
                cpus=record.cpu_quota,
                memory_mb=record.memory_mb,
                pids_limit=record.pids_limit,
            ),
            workspace_volume=workspace_volume,
        )

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
