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

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal, Protocol
from uuid import UUID, uuid4

from helix_agent.common.observability import helix_histogram
from helix_agent.persistence import UserWorkspaceStore, workspace_volume_name
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
    WorkspaceFileNotFoundError,
    WorkspaceFileTooLargeError,
)
from sandbox_supervisor.quota_enforcer import QuotaEnforcer
from sandbox_supervisor.runner_link import ExecResult, RunnerLink, RunnerLinkError
from sandbox_supervisor.schemas import AcquireRequest, AcquireResponse
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.store import SandboxStore

DESTROY_REASON_WORKSPACE_SOFT_DELETE = "workspace_soft_delete"

logger = logging.getLogger(__name__)


# Stream K.K10 — Sandbox cold-start duration. Measured from the moment
# ``acquire`` decides to launch (after quota + workspace resolution) to
# the moment ``wait_ready`` returns. Warm-session reuse (Stream J.15
# warm path) does not observe — those acquires never run docker. SLO #4
# (slo.md): P95 < 3s (M0) / < 500ms (M1 with a warm pool).
_sandbox_cold_start_seconds = helix_histogram(
    "helix_sandbox_cold_start_seconds",
    "Seconds from launch decision to ``wait_ready`` success.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)


class AuditSink(Protocol):
    """The audit surface the supervisor needs — :class:`AuditLogger` satisfies it."""

    async def write(self, entry: AuditEntry) -> None:
        """Persist one audit entry."""


def _container_name(sandbox_id: UUID) -> str:
    """The deterministic ``--name`` for a sandbox's container."""
    return f"helix-sb-{sandbox_id}"


#: Per-file download cap for the J.9 workspace-file read. Artifacts are
#: documents / code / data — small; the cap bounds the supervisor's
#: in-memory buffer against a pathological file.
_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


def _validate_workspace_path(path: str) -> str:
    """Reject a non-relative or ``..``-bearing workspace path (J.9).

    ``save_artifact`` already validates this, but the path round-trips
    through the control-plane untrusted — re-check at this boundary.
    """
    cleaned = path.strip()
    if not cleaned or cleaned.startswith("/") or ".." in PurePosixPath(cleaned).parts:
        msg = f"workspace path must be relative and free of '..': {path!r}"
        raise WorkspaceFileNotFoundError(msg)
    return cleaned


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
        quota_enforcer: QuotaEnforcer | None = None,
    ) -> None:
        self._store = store
        self._docker = docker
        self._audit = audit
        self._runtime = runtime_provider
        self._workspaces = workspace_store
        self._settings = settings
        # Stream J.15-补强-1 — per-workspace volume quota gate. ``None``
        # means quota enforcement is disabled (legacy callers + the
        # ephemeral-tmpfs path; the J.15 warm-session path always
        # constructs one in ``create_app``).
        self._quota_enforcer = quota_enforcer
        # Held runner links, keyed by sandbox id — the option-C transport.
        self._links: dict[UUID, RunnerLink] = {}
        # Stream J.15 — warm per-user sandbox sessions: ``(tenant, user)``
        # → the live sandbox id. An ``acquire`` with a ``user_id`` reuses
        # the session here; the idle reaper / destroy clears the entry.
        self._sessions: dict[tuple[UUID, UUID], UUID] = {}
        # Per-sandbox exec lock — the held pipe handles one exec at a
        # time, so concurrent runs sharing a warm session serialise here.
        self._exec_locks: dict[UUID, asyncio.Lock] = {}

    async def acquire(self, request: AcquireRequest) -> AcquireResponse:
        """Reuse the caller's warm session, or launch a fresh sandbox.

        Stream J.15 — a user-scoped acquire reuses that user's warm
        sandbox session if one is live (no ``docker run``); the idle
        reaper reclaims it once unused for ``session_idle_ttl_s``.

        Raises :class:`QuotaExceededError` when the tenant is at its
        sandbox cap, :class:`WorkspaceQuotaExceededError` when the
        user's workspace volume is at its size ceiling (J.15-补强-1,
        Mini-ADR J-29 第 1 项), :class:`WorkspaceDeletedError` when
        the workspace has been soft-deleted (Mini-ADR J-36), and
        :class:`SupervisorError` when the container fails to launch.
        """
        if request.user_id is not None:
            reused = await self._reuse_session(request.tenant_id, request.user_id)
            if reused is not None:
                return reused

        await self._enforce_quota(request.tenant_id)

        # A user-scoped acquire mounts that user's persistent workspace
        # volume at /workspace; resolve (creating on first use) the
        # user_workspace row. No user_id → an ephemeral tmpfs workspace.
        workspace: UserWorkspace | None = None
        if request.user_id is not None:
            workspace = await self._workspaces.resolve(
                tenant_id=request.tenant_id, user_id=request.user_id
            )
            # J.15-补强-1: enforce per-workspace quota + soft-delete
            # before paying for a docker launch. Raises on reject;
            # ``QuotaEnforcer.check`` emits the audit before raising.
            if self._quota_enforcer is not None:
                await self._quota_enforcer.check(workspace=workspace)

        record = self._new_record(request, workspace=workspace)
        await self._store.insert(record)

        workspace_volume = workspace.volume_name if workspace is not None else None
        # Stream K.K10 — cold-start measurement starts here (after quota
        # + workspace resolution; before the actual docker launch).
        cold_start_started = time.monotonic()
        try:
            link = await self._docker.launch(
                self._run_argv(record, workspace_volume=workspace_volume)
            )
            await link.wait_ready(self._settings.runner_ready_timeout_s)
        except (DockerError, RunnerLinkError) as exc:
            await self._store.update(record.with_state(SandboxState.FAILED))
            msg = f"sandbox launch failed: {exc}"
            raise SupervisorError(msg) from exc
        _sandbox_cold_start_seconds.observe(time.monotonic() - cold_start_started)

        self._links[record.id] = link
        acquired_at = datetime.now(UTC)
        await self._store.update(
            record.with_state(
                SandboxState.IN_USE,
                container_id=_container_name(record.id),
                acquired_at=acquired_at,
            )
        )
        if request.user_id is not None:
            # Register the warm session for reuse by the user's next run.
            self._sessions[(request.tenant_id, request.user_id)] = record.id
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

        The per-sandbox lock serialises concurrent execs sharing one
        warm session (the held pipe handles one exec at a time); each
        exec stamps ``last_used_at`` so the idle reaper measures from it.
        """
        link = self._links.get(sandbox_id)
        if link is None:
            raise SandboxNotFoundError(sandbox_id)
        resolved_timeout = timeout_s if timeout_s is not None else self._settings.default_timeout_s
        lock = self._exec_locks.setdefault(sandbox_id, asyncio.Lock())
        async with lock:
            await self._touch(sandbox_id)
            try:
                return await link.exec(code, resolved_timeout)
            except RunnerLinkError as exc:
                msg = f"sandbox exec failed: {exc}"
                raise SupervisorError(msg) from exc

    async def release(self, sandbox_id: UUID) -> None:
        """Routine teardown.

        A J.15 warm per-user session is **kept alive** (no-op) — it
        stays hot for the user's next run and is reclaimed by the idle
        reaper. A non-session (no ``user_id``) sandbox is destroyed.

        J.15-补强-1 — before keeping warm, fires a fire-and-forget
        :meth:`QuotaEnforcer.refresh_size` so the next acquire's quota
        check uses a fresh ``size_bytes`` measurement.
        """
        record = await self._store.get(sandbox_id)
        if (
            record is not None
            and record.user_id is not None
            and record.state is SandboxState.IN_USE
        ):
            await self._schedule_size_refresh(record.tenant_id, record.user_id)
            return
        await self.destroy(sandbox_id, reason=DESTROY_REASON_RELEASE)

    async def _schedule_size_refresh(self, tenant_id: UUID, user_id: UUID) -> None:
        """Fire-and-forget :meth:`QuotaEnforcer.refresh_size` (J.15-补强-1).

        Schedules an async task so the release path is not blocked on
        the ``du`` measure. The enforcer swallows its own errors —
        nothing here needs to handle them.
        """
        if self._quota_enforcer is None:
            return
        workspace = await self._workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
        enforcer = self._quota_enforcer
        asyncio.create_task(enforcer.refresh_size(workspace=workspace))

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
        self._exec_locks.pop(sandbox_id, None)
        # Clear the warm-session entry — but only if it still points here
        # (a newer session for the same user may have replaced it).
        if record.user_id is not None:
            session_key = (record.tenant_id, record.user_id)
            if self._sessions.get(session_key) == sandbox_id:
                del self._sessions[session_key]
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

    async def mark_workspace_deleted(self, *, tenant_id: UUID, user_id: UUID) -> None:
        """Soft-delete a user's workspace (Mini-ADR J-36 lifecycle 第 2 档).

        Idempotent. After this call:

        * Subsequent ``acquire(user_id=this_user)`` raises
          :class:`WorkspaceDeletedError` (Mini-ADR J-36).
        * Any live warm session is force-destroyed — the next acquire
          would reject anyway, but freeing the slot immediately keeps
          quota state honest.
        * The reaper picks the row up on its next sweep and triggers the
          archive job (J.15-补强-2 backup pipeline reuses the same
          mechanism). Until then the volume stays on disk but is
          inaccessible.
        * Emits ``workspace:soft_delete`` audit (Stream J.15-补强-1).
        """
        workspace = await self._workspaces.resolve(tenant_id=tenant_id, user_id=user_id)
        # Idempotent: skip the audit + destroy when already deleted.
        if workspace.deleted_at is not None:
            return
        now = datetime.now(UTC)
        await self._workspaces.soft_delete(workspace_id=workspace.id, now=now)
        session_key = (tenant_id, user_id)
        sandbox_id = self._sessions.get(session_key)
        if sandbox_id is not None:
            await self.destroy(sandbox_id, reason=DESTROY_REASON_WORKSPACE_SOFT_DELETE)
        await self._emit_audit(
            tenant_id=tenant_id,
            action=AuditAction.WORKSPACE_SOFT_DELETE,
            result=AuditResult.SUCCESS,
            sandbox_id=None,
            details={
                "user_id": str(user_id),
                "workspace_id": str(workspace.id),
                "volume_name": workspace.volume_name,
            },
            resource_type="user_workspace",
            resource_id=str(workspace.id),
        )

    async def read_workspace_file(self, *, tenant_id: UUID, user_id: UUID, path: str) -> bytes:
        """Read a file from a user's persistent workspace volume (Stream J.9).

        Backs artifact content download — the control-plane proxies to
        here because only the supervisor can read a docker volume.
        Raises :class:`WorkspaceFileNotFoundError` when the file is
        missing / unreadable, and :class:`WorkspaceFileTooLargeError`
        when it exceeds the download cap.
        """
        safe_path = _validate_workspace_path(path)
        volume = workspace_volume_name(tenant_id, user_id)
        try:
            data = await self._docker.read_volume_file(
                volume=volume,
                path=safe_path,
                image=self._settings.sandbox_image,
                max_bytes=_MAX_ARTIFACT_BYTES,
            )
        except DockerError as exc:
            raise WorkspaceFileNotFoundError(str(exc)) from exc
        if len(data) > _MAX_ARTIFACT_BYTES:
            msg = f"workspace file {path!r} exceeds the {_MAX_ARTIFACT_BYTES}-byte download cap"
            raise WorkspaceFileTooLargeError(msg)
        return data

    # ------------------------------------------------------------------

    async def _reuse_session(self, tenant_id: UUID, user_id: UUID) -> AcquireResponse | None:
        """Return the user's warm session as an :class:`AcquireResponse`, or
        ``None`` when there is no live session to reuse (J.15).

        Tenant-level sandbox-count quota is skipped (the session already
        holds its slot). But J.15-补强-1 still rechecks per-workspace
        size quota + soft-delete state — the volume can grow past its
        ``size_limit_bytes`` between acquires, and a workspace can be
        soft-deleted while a session is warm; both must reject reuse.
        A stale map entry (link gone, or the row is no longer
        ``IN_USE``) is dropped so the caller falls through.
        """
        sandbox_id = self._sessions.get((tenant_id, user_id))
        if sandbox_id is None or sandbox_id not in self._links:
            return None
        record = await self._store.get(sandbox_id)
        if record is None or record.state is not SandboxState.IN_USE:
            self._sessions.pop((tenant_id, user_id), None)
            return None
        # J.15-补强-1: workspace-level quota + soft-delete recheck on reuse.
        if self._quota_enforcer is not None:
            workspace = await self._workspaces.resolve(
                tenant_id=tenant_id, user_id=user_id
            )
            # Raises WorkspaceQuotaExceededError / WorkspaceDeletedError
            # — let the caller propagate; the warm session entry stays
            # (the reaper / explicit destroy will clear it).
            await self._quota_enforcer.check(workspace=workspace)
        return AcquireResponse(
            sandbox_id=sandbox_id,
            container_id=_container_name(sandbox_id),
            cold_start=False,
            acquired_at=record.acquired_at or record.created_at,
        )

    async def _touch(self, sandbox_id: UUID) -> None:
        """Stamp ``last_used_at`` so the idle reaper measures from now."""
        record = await self._store.get(sandbox_id)
        if record is not None:
            await self._store.update(
                record.with_state(record.state, last_used_at=datetime.now(UTC))
            )

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
        resource_type: Literal["sandbox", "user_workspace"] = "sandbox",
        resource_id: str | None = None,
    ) -> None:
        """Emit one audit entry. ``resource_id`` overrides ``sandbox_id`` when provided.

        J.15-补强-1 — ``resource_type`` defaults to ``"sandbox"`` for the
        long-standing F.1 callers; ``mark_workspace_deleted`` passes
        ``"user_workspace"`` so the workspace audit trail is separable
        from sandbox lifecycle events.
        """
        resolved_resource_id = (
            resource_id
            if resource_id is not None
            else (str(sandbox_id) if sandbox_id is not None else None)
        )
        await self._audit.write(
            AuditEntry(
                tenant_id=tenant_id,
                actor_type="system",
                actor_id=self._settings.service_name,
                action=action,
                resource_type=resource_type,
                resource_id=resolved_resource_id,
                result=result,
                reason=reason,
                details=details,
            )
        )
