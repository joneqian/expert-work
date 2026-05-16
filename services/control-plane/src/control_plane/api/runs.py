"""``POST /v1/sessions/{thread_id}/runs`` — SSE run trigger.

Stream B.7 shipped a *fake* stream; the control-plane cutover replaces
it with the real path. In-process monolith (STREAM-E-DESIGN § 2.6): the
endpoint loads the thread's agent manifest, builds (or cache-hits) a
runnable agent via the orchestrator's :func:`build_agent`, spawns the
E.14 ``run_agent`` worker as a background task, and streams the worker's
events back through E.14 ``sse_consumer``.

SSE event vocabulary is ``metadata`` / ``updates`` / ``end`` / ``error``
plus ``: heartbeat`` comment frames — see the amended ADR B-4. The old
``token`` / ``done`` words were fake-stream placeholders.

Cancellation: ``sse_consumer`` polls ``request.is_disconnected`` and, on
disconnect, cancels the run through the :class:`RunManager` (E.15
cooperative cancellation surfaces it inside the graph).

Audit: a single ``session:write`` row lands at run start. Run-completion
lifecycle audit belongs with the orchestrator and is a follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field

from control_plane.api._quota_admission import check_admission
from control_plane.audit import emit
from control_plane.quota.base import QuotaService
from control_plane.runtime import AgentRuntime
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.protocol import AuditAction, ThreadStatus
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator import AgentFactoryError, run_agent, sse_consumer

logger = logging.getLogger("helix.control_plane.runs")


class RunRequest(BaseModel):
    """POST body. ``input`` is the user's prompt for this run."""

    model_config = ConfigDict(extra="forbid")

    input: str | None = Field(default=None, max_length=8192)


def _get_thread_repo(request: Request) -> object:
    return request.app.state.thread_meta_repo


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_quota(request: Request) -> QuotaService:
    return request.app.state.quota_service  # type: ignore[no-any-return]


def _get_agent_repo(request: Request) -> AgentSpecStore:
    return request.app.state.agent_spec_repo  # type: ignore[no-any-return]


def _get_agent_runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime  # type: ignore[no-any-return]


def build_runs_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

    @router.post("/{thread_id}/runs", response_model=None)
    async def trigger_run(
        thread_id: UUID,
        payload: RunRequest,
        request: Request,
        threads: Annotated[object, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        quota: Annotated[QuotaService, Depends(_get_quota)],
        agent_repo: Annotated[AgentSpecStore, Depends(_get_agent_repo)],
        runtime: Annotated[AgentRuntime, Depends(_get_agent_runtime)],
    ) -> StreamingResponse | JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = request.state.actor_id
        trace_id = current_trace_id_hex()

        meta = await threads.get(thread_id, tenant_id=tenant_id)  # type: ignore[attr-defined]
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        if meta.status is not ThreadStatus.ACTIVE:
            raise HTTPException(
                status_code=409,
                detail=f"session is {meta.status.value}; only active sessions accept runs",
            )
        if meta.agent_name is None or meta.agent_version is None:
            raise HTTPException(status_code=409, detail="session is not bound to an agent")

        # Admission (Stream C.5b): bucket the run against the bound
        # agent. Denial returns 429 + Retry-After and audits — no stream.
        denial = await check_admission(
            quota=quota,
            audit=audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            agent=meta.agent_name,
            resource_kind="run",
        )
        if denial is not None:
            return denial

        # Load the agent manifest + build (cache-hit) a runnable agent.
        record = await agent_repo.get(
            tenant_id=tenant_id, name=meta.agent_name, version=meta.agent_version
        )
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"agent {meta.agent_name}@{meta.agent_version} not found",
            )
        try:
            built = await runtime.get_agent(
                tenant_id=tenant_id,
                name=meta.agent_name,
                version=meta.agent_version,
                spec=record.spec,
            )
        except AgentFactoryError as exc:
            raise HTTPException(
                status_code=422, detail=f"agent manifest cannot be built: {exc}"
            ) from exc

        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SESSION_WRITE,
            resource_type="session",
            resource_id=str(thread_id),
            trace_id=trace_id,
            details={"stage": "run.start", "input_len": len(payload.input or "")},
        )

        # Register the run + spawn the background worker. The worker
        # streams graph events into the bridge; sse_consumer drains them.
        run_id = uuid4()
        run_record = await runtime.run_manager.create(
            run_id=run_id, thread_id=thread_id, tenant_id=tenant_id
        )
        graph_input = {
            "messages": [
                SystemMessage(content=built.system_prompt),
                HumanMessage(content=payload.input or ""),
            ],
            "step_count": 0,
            "max_steps": built.max_steps,
        }
        config: RunnableConfig = {
            "configurable": {
                "thread_id": str(thread_id),
                "tenant_id": str(tenant_id),
                "run_id": str(run_id),
            }
        }
        worker = asyncio.create_task(
            run_agent(
                bridge=runtime.stream_bridge,
                run_manager=runtime.run_manager,
                record=run_record,
                # CompiledStateGraph structurally satisfies the
                # StreamableGraph Protocol at runtime (its astream is
                # overloaded, which mypy can't match to the Protocol's
                # single signature) — proven by test_sse.py.
                graph=built.graph,  # type: ignore[arg-type]
                graph_input=graph_input,
                config=config,
            )
        )
        await runtime.run_manager.attach_task(run_id, worker)
        logger.info(
            "control_plane.run.started run_id=%s thread=%s agent=%s@%s",
            run_id,
            thread_id,
            meta.agent_name,
            meta.agent_version,
        )

        return StreamingResponse(
            sse_consumer(
                bridge=runtime.stream_bridge,
                record=run_record,
                run_manager=runtime.run_manager,
                is_disconnected=request.is_disconnected,
                last_event_id=request.headers.get("Last-Event-ID"),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Helix-Run-Id": str(run_id),
            },
        )

    return router
