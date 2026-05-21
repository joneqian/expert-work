"""FastAPI application — the supervisor's internal HTTP surface.

``create_app`` builds the real service (DB-backed store, CLI Docker
client, audit logger, TTL reaper). Tests inject a pre-built
:class:`SandboxSupervisor` to exercise the routes without Docker / a DB.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from helix_agent.persistence import (
    DatabaseConfig,
    SqlAuditLogStore,
    SqlUserWorkspaceStore,
    SqlVolumeBackupDLQ,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.runtime.audit.fallback import InMemoryAuditFallbackQueue
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.audit.redactor import DefaultSecretRedactor
from helix_agent.runtime.sandbox import make_sandbox_runtime_provider
from helix_agent.runtime.storage import ObjectStore, S3CompatibleConfig, make_object_store
from sandbox_supervisor.docker_client import CliDockerClient
from sandbox_supervisor.domain import (
    QuotaExceededError,
    SandboxNotFoundError,
    SupervisorError,
    WorkspaceDeletedError,
    WorkspaceFileNotFoundError,
    WorkspaceFileTooLargeError,
    WorkspaceQuotaExceededError,
)
from sandbox_supervisor.lifecycle import VolumeLifecycleManager
from sandbox_supervisor.quota_enforcer import QuotaEnforcer
from sandbox_supervisor.reaper import SandboxReaper
from sandbox_supervisor.schemas import (
    AcquireRequest,
    AcquireResponse,
    DestroyRequest,
    ExecRequest,
    ExecResponse,
    HealthResponse,
)
from sandbox_supervisor.settings import SandboxSupervisorSettings
from sandbox_supervisor.store import DbSandboxStore
from sandbox_supervisor.supervisor import SandboxSupervisor

logger = logging.getLogger(__name__)


def get_supervisor(request: Request) -> SandboxSupervisor:
    """FastAPI dependency — the live supervisor held on ``app.state``."""
    supervisor: SandboxSupervisor = request.app.state.supervisor
    return supervisor


#: ``Annotated`` dependency alias — keeps ``Depends`` out of argument
#: defaults (flake8-bugbear B008), the modern FastAPI idiom.
SupervisorDep = Annotated[SandboxSupervisor, Depends(get_supervisor)]


def create_app(
    settings: SandboxSupervisorSettings | None = None,
    *,
    supervisor: SandboxSupervisor | None = None,
    enable_reaper: bool = True,
) -> FastAPI:
    """Build the FastAPI app.

    ``supervisor`` injects a pre-built supervisor (tests) — the lifespan
    then skips all DB / Docker wiring and the reaper.
    """
    resolved_settings = settings or SandboxSupervisorSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if supervisor is not None:
            app.state.supervisor = supervisor
            yield
            return

        engine = create_async_engine_from_config(
            DatabaseConfig(dsn=resolved_settings.db_dsn, echo_sql=resolved_settings.db_echo)
        )
        session_factory = create_async_session_factory(engine)
        store = DbSandboxStore(session_factory)
        audit = AuditLogger(
            store=SqlAuditLogStore(session_factory),
            redactor=DefaultSecretRedactor(),
            fallback=InMemoryAuditFallbackQueue(),
        )
        docker = CliDockerClient()
        # Startup recovery: clear any sandboxes a previous supervisor left
        # behind — the held-pipe model (option C) does not survive a
        # supervisor restart, so leftovers must be swept.
        await docker.sweep_orphans()
        workspace_store = SqlUserWorkspaceStore(session_factory)
        quota_enforcer = QuotaEnforcer(
            workspace_store=workspace_store,
            audit=audit,
            docker=docker,
            measure_image=resolved_settings.sandbox_image,
            service_name=resolved_settings.service_name,
        )
        live = SandboxSupervisor(
            store=store,
            docker=docker,
            audit=audit,
            runtime_provider=make_sandbox_runtime_provider(resolved_settings.oci_runtime),
            workspace_store=workspace_store,
            settings=resolved_settings,
            quota_enforcer=quota_enforcer,
        )
        app.state.supervisor = live

        stop = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []
        # Stream J.15-补强-2 — wire the volume lifecycle (archive +
        # backup + DLQ retry). The ObjectStore is held inside the
        # ``async with`` so its tear-down happens after the reaper /
        # daily task have shut down.
        object_store_cm = _build_object_store(resolved_settings)
        async with object_store_cm as object_store:
            lifecycle: VolumeLifecycleManager | None = None
            if resolved_settings.workspace_lifecycle_enabled:
                lifecycle = VolumeLifecycleManager(
                    workspace_store=workspace_store,
                    dlq=SqlVolumeBackupDLQ(session_factory),
                    docker=docker,
                    object_store=object_store,
                    settings=resolved_settings,
                    audit=audit,
                    service_name=resolved_settings.service_name,
                )
            if enable_reaper:
                reaper = SandboxReaper(
                    supervisor=live,
                    store=store,
                    interval_s=resolved_settings.reaper_interval_s,
                    idle_ttl_s=resolved_settings.session_idle_ttl_s,
                    lifecycle=lifecycle,
                )
                tasks.append(asyncio.create_task(reaper.run_forever(stop)))
                if lifecycle is not None and resolved_settings.workspace_backup_hour >= 0:
                    tasks.append(
                        asyncio.create_task(_run_daily_backup(lifecycle, resolved_settings, stop))
                    )
            logger.info(
                "sandbox_supervisor.start reaper=%s lifecycle=%s backup_hour=%d",
                enable_reaper,
                lifecycle is not None,
                resolved_settings.workspace_backup_hour,
            )
            try:
                yield
            finally:
                stop.set()
                if tasks:
                    # gather (not a bare ``await``) so CodeQL does not
                    # misread the awaits as ineffectual statements.
                    await asyncio.gather(*tasks, return_exceptions=True)
                await engine.dispose()
                logger.info("sandbox_supervisor.stop")

    app = FastAPI(title="Helix Sandbox Supervisor", lifespan=lifespan)
    _register_routes(app)
    _register_exception_handlers(app)
    return app


def _build_object_store(
    settings: SandboxSupervisorSettings,
) -> contextlib.AbstractAsyncContextManager[ObjectStore]:
    """Resolve the ObjectStore backend from settings (Stream J.15-补强-2).

    Returns an ``async with`` context manager that yields the live
    :class:`ObjectStore`. ``memory`` is the dev / CI default;
    ``s3-compatible`` fills in the S3 / MinIO / OSS credentials.
    """
    if settings.object_store_backend == "memory":
        return make_object_store("memory")
    config = S3CompatibleConfig(
        endpoint_url=settings.object_store_endpoint_url,
        region=settings.object_store_region,
        bucket=settings.object_store_bucket,
        access_key=settings.object_store_access_key,
        secret_key=settings.object_store_secret_key,
        use_path_style=settings.object_store_use_path_style,
    )
    return make_object_store("s3-compatible", config)


async def _run_daily_backup(
    lifecycle: VolumeLifecycleManager,
    settings: SandboxSupervisorSettings,
    stop: asyncio.Event,
) -> None:
    """Sleep until the configured hour each day, then run a backup sweep.

    Stream J.15-补强-2 Mini-ADR J-29 第 2 项. The loop computes the wall-
    clock delta to the next ``workspace_backup_hour`` and waits there,
    so the worker doesn't busy-poll. The first iteration may fire
    immediately when starting after the configured hour — that is
    intentional (cron semantics on supervisor restart).
    """
    backup_hour = settings.workspace_backup_hour
    while not stop.is_set():
        now = datetime.now(UTC)
        target = now.replace(hour=backup_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        delay = (target - now).total_seconds()
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            return  # stop signaled — leave the loop without firing.
        except TimeoutError:
            pass  # the delay elapsed — run the sweep.
        try:
            await lifecycle.backup_active(now=datetime.now(UTC))
        except Exception:
            logger.exception("sandbox_supervisor.daily_backup_failed")


def _register_routes(app: FastAPI) -> None:
    @app.post("/v1/sandboxes:acquire")
    async def acquire(body: AcquireRequest, supervisor: SupervisorDep) -> AcquireResponse:
        return await supervisor.acquire(body)

    @app.post("/v1/sandboxes/{sandbox_id}:release", status_code=204)
    async def release(sandbox_id: UUID, supervisor: SupervisorDep) -> Response:
        await supervisor.release(sandbox_id)
        return Response(status_code=204)

    @app.post("/v1/sandboxes/{sandbox_id}:destroy", status_code=204)
    async def destroy(
        sandbox_id: UUID, body: DestroyRequest, supervisor: SupervisorDep
    ) -> Response:
        await supervisor.destroy(sandbox_id, reason=body.reason)
        return Response(status_code=204)

    @app.post("/v1/sandboxes/{sandbox_id}:exec")
    async def exec_code(
        sandbox_id: UUID, body: ExecRequest, supervisor: SupervisorDep
    ) -> ExecResponse:
        result = await supervisor.exec(sandbox_id, code=body.code, timeout_s=body.timeout_s)
        return ExecResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )

    @app.get("/v1/workspaces/{tenant_id}/{user_id}/file")
    async def read_workspace_file(
        tenant_id: UUID, user_id: UUID, path: str, supervisor: SupervisorDep
    ) -> Response:
        # Stream J.9 — artifact content download. Only the supervisor can
        # read a per-user docker volume; the control-plane proxies here.
        data = await supervisor.read_workspace_file(tenant_id=tenant_id, user_id=user_id, path=path)
        return Response(content=data, media_type="application/octet-stream")

    @app.get("/v1/health")
    async def health(supervisor: SupervisorDep) -> HealthResponse:
        docker_ok = await supervisor.docker_ok()
        return HealthResponse(status="ok" if docker_ok else "degraded", docker_ok=docker_ok)


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(QuotaExceededError)
    async def _quota(_request: Request, exc: QuotaExceededError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(WorkspaceQuotaExceededError)
    async def _workspace_quota(_request: Request, exc: WorkspaceQuotaExceededError) -> JSONResponse:
        # Stream J.15-补强-1 (Mini-ADR J-29 第 1 项): per-workspace size
        # quota — same 429 status as the sandbox-count quota.
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(WorkspaceDeletedError)
    async def _workspace_deleted(_request: Request, exc: WorkspaceDeletedError) -> JSONResponse:
        # Stream J.15-补强-1 (Mini-ADR J-36): the workspace was soft-
        # deleted; recovery is a separate operator action (推 M1).
        return JSONResponse(status_code=410, content={"detail": str(exc)})

    @app.exception_handler(SandboxNotFoundError)
    async def _not_found(_request: Request, exc: SandboxNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(WorkspaceFileNotFoundError)
    async def _file_not_found(_request: Request, exc: WorkspaceFileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(WorkspaceFileTooLargeError)
    async def _file_too_large(_request: Request, exc: WorkspaceFileTooLargeError) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    @app.exception_handler(SupervisorError)
    async def _supervisor_error(_request: Request, exc: SupervisorError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
