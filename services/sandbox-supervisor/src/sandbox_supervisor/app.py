"""FastAPI application — the supervisor's internal HTTP surface.

``create_app`` builds the real service (DB-backed store, CLI Docker
client, audit logger, TTL reaper). Tests inject a pre-built
:class:`SandboxSupervisor` to exercise the routes without Docker / a DB.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from helix_agent.persistence import (
    DatabaseConfig,
    SqlAuditLogStore,
    SqlUserWorkspaceStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.runtime.audit.fallback import InMemoryAuditFallbackQueue
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.audit.redactor import DefaultSecretRedactor
from helix_agent.runtime.sandbox import make_sandbox_runtime_provider
from sandbox_supervisor.docker_client import CliDockerClient
from sandbox_supervisor.domain import QuotaExceededError, SandboxNotFoundError, SupervisorError
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
        live = SandboxSupervisor(
            store=store,
            docker=docker,
            audit=audit,
            runtime_provider=make_sandbox_runtime_provider(resolved_settings.oci_runtime),
            workspace_store=SqlUserWorkspaceStore(session_factory),
            settings=resolved_settings,
        )
        app.state.supervisor = live

        stop = asyncio.Event()
        reaper_task: asyncio.Task[None] | None = None
        if enable_reaper:
            reaper = SandboxReaper(
                supervisor=live,
                store=store,
                interval_s=resolved_settings.reaper_interval_s,
                grace_s=resolved_settings.reaper_grace_s,
            )
            reaper_task = asyncio.create_task(reaper.run_forever(stop))
        logger.info("sandbox_supervisor.start reaper=%s", enable_reaper)
        try:
            yield
        finally:
            stop.set()
            if reaper_task is not None:
                # gather (not a bare ``await reaper_task``) so CodeQL does
                # not misread the await as an ineffectual statement.
                await asyncio.gather(reaper_task)
            await engine.dispose()
            logger.info("sandbox_supervisor.stop")

    app = FastAPI(title="Helix Sandbox Supervisor", lifespan=lifespan)
    _register_routes(app)
    _register_exception_handlers(app)
    return app


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

    @app.get("/v1/health")
    async def health(supervisor: SupervisorDep) -> HealthResponse:
        docker_ok = await supervisor.docker_ok()
        return HealthResponse(status="ok" if docker_ok else "degraded", docker_ok=docker_ok)


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(QuotaExceededError)
    async def _quota(_request: Request, exc: QuotaExceededError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(SandboxNotFoundError)
    async def _not_found(_request: Request, exc: SandboxNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SupervisorError)
    async def _supervisor_error(_request: Request, exc: SupervisorError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
