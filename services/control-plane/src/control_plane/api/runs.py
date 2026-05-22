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

Audit: a ``session:write`` row lands at run start; the ``run_agent``
worker writes the run-completion row (``run:completed`` / ``run:failed``)
at run end.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator

from control_plane.api._quota_admission import check_admission
from control_plane.api._user_scope import (
    caller_owns_thread,
    get_user_repo,
    resolve_caller_user_id,
)
from control_plane.audit import emit
from control_plane.quota.base import QuotaService
from control_plane.runtime import AgentRuntime
from control_plane.settings import Settings
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence import ApprovalStore
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.rls import current_user_id_var
from helix_agent.persistence.tenant_user import TenantUserStore
from helix_agent.protocol import ApprovalStatus, AuditAction, ThreadStatus
from helix_agent.protocol.multimodal import parse_image_ref
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator import AgentFactoryError, run_agent, sse_consumer
from orchestrator.multimodal import image_ref_block

logger = logging.getLogger("helix.control_plane.runs")


class RunRequest(BaseModel):
    """POST body. ``input`` is the user's prompt for this run;
    ``image_refs`` is the list of J.6 ``helix://image/...`` references
    uploaded via ``POST /v1/sessions/{thread_id}/uploads``."""

    model_config = ConfigDict(extra="forbid")

    input: str | None = Field(default=None, max_length=8192)
    image_refs: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("image_refs")
    @classmethod
    def _parse_image_refs(cls, value: list[str]) -> list[str]:
        for ref in value:
            parse_image_ref(ref)  # raises ValueError if malformed → 422
        return value


def _get_thread_repo(request: Request) -> object:
    return request.app.state.thread_meta_repo


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _validate_image_refs(
    refs: list[str],
    *,
    tenant_id: UUID,
    thread_id: UUID,
    supports_vision: bool,
    has_vision_block: bool,
    max_per_run: int,
) -> None:
    """Enforce the J.6 run-time image-ref constraints.

    Raises :class:`HTTPException` with the right status:
    * **422** when the agent is image-incapable and no ``vision:`` block
      is declared, or when the count exceeds ``max_per_run``;
    * **404** when a ref belongs to a different tenant or thread —
      hides cross-scope existence per the J.14 pattern.
    """
    if not refs:
        return
    if not supports_vision and not has_vision_block:
        raise HTTPException(
            status_code=422,
            detail=(
                "agent does not accept image input: model.supports_vision is "
                "false and no 'vision' block is declared"
            ),
        )
    if len(refs) > max_per_run:
        raise HTTPException(
            status_code=422,
            detail=f"too many images: max {max_per_run} per run",
        )
    for ref_str in refs:
        ref = parse_image_ref(ref_str)
        if ref.tenant_id != tenant_id or ref.thread_id != thread_id:
            raise HTTPException(status_code=404, detail="image ref not found")


def _build_human_message(
    *, input_text: str | None, image_refs: list[str], supports_vision: bool
) -> HumanMessage:
    """Assemble the ``HumanMessage`` for a J.6 multimodal run input.

    Path A (``supports_vision=True``) — emit a content-block list with
    the text followed by one ``image_ref`` block per upload, so the
    provider adapter resolves them to native multimodal payloads.

    Path B (``supports_vision=False`` with images) — emit plain text
    with each ref mentioned as ``[image attached: helix://...]``. The
    agent has the ``ask_image`` tool in its catalogue and uses these
    refs to call it.

    No-images case — emit plain text unchanged.
    """
    text = input_text or ""
    if not image_refs:
        return HumanMessage(content=text)
    if supports_vision:
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        for ref in image_refs:
            content.append(image_ref_block(ref))
        return HumanMessage(content=content)
    mentions = "\n".join(f"[image attached: {ref}]" for ref in image_refs)
    body = f"{text}\n\n{mentions}" if text else mentions
    return HumanMessage(content=body)


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_quota(request: Request) -> QuotaService:
    return request.app.state.quota_service  # type: ignore[no-any-return]


def _get_agent_repo(request: Request) -> AgentSpecStore:
    return request.app.state.agent_spec_repo  # type: ignore[no-any-return]


def _get_agent_runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime  # type: ignore[no-any-return]


def _get_approval_store(request: Request) -> ApprovalStore:
    return request.app.state.approval_store  # type: ignore[no-any-return]


def build_runs_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

    @router.post("/{thread_id}/runs", response_model=None)
    async def trigger_run(
        thread_id: UUID,
        payload: RunRequest,
        request: Request,
        threads: Annotated[object, Depends(_get_thread_repo)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        quota: Annotated[QuotaService, Depends(_get_quota)],
        agent_repo: Annotated[AgentSpecStore, Depends(_get_agent_repo)],
        runtime: Annotated[AgentRuntime, Depends(_get_agent_runtime)],
        settings: Annotated[Settings, Depends(_get_settings)],
        approvals: Annotated[ApprovalStore, Depends(_get_approval_store)],
    ) -> StreamingResponse | JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = request.state.actor_id
        trace_id = current_trace_id_hex()

        # Stream K.K2 (Mini-ADR K-2) — SSE cross-tenant safety lives here.
        # ``threads.get(thread_id, tenant_id=tenant_id)`` 404s when the
        # thread belongs to a different tenant, so the SSE stream never
        # opens for a cross-tenant caller. No duplicate guard at the
        # SSE layer (Mini-ADR K-2); the invariant is locked by
        # tests/test_runs_api.py::test_runs_cross_tenant_sse_rejected.
        meta = await threads.get(thread_id, tenant_id=tenant_id)  # type: ignore[attr-defined]
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        # Stream J.14 — a user-owned thread accepts runs only from its
        # owner (or an admin); 404 so cross-user existence stays hidden.
        caller_user_id = await resolve_caller_user_id(request, users)
        if not caller_owns_thread(
            meta=meta, caller_user_id=caller_user_id, principal=request.state.principal
        ):
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

        # Stream J.6 — enforce image-ref invariants before any side effects.
        _validate_image_refs(
            payload.image_refs,
            tenant_id=tenant_id,
            thread_id=thread_id,
            supports_vision=built.supports_vision,
            has_vision_block=record.spec.spec.vision is not None,
            max_per_run=settings.multimodal_max_images_per_run,
        )

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
        #
        # Stream K.K10 — the SSE worker observes the durable-resume
        # histogram only on runs that resumed an existing checkpoint.
        # Per-thread prior runs in the manager are an in-process proxy
        # for that signal — first run on a thread is cold, second+ is
        # a resume. A truly checkpoint-aware probe would inspect the
        # PostgresSaver state, but the prior-runs check is cheap and
        # correct for the SLO #5 question ("did the user wait through
        # a resume?").
        run_id = uuid4()
        prior_runs = await runtime.run_manager.list_by_thread(thread_id, tenant_id=tenant_id)
        run_record = await runtime.run_manager.create(
            run_id=run_id,
            thread_id=thread_id,
            tenant_id=tenant_id,
            is_resume=bool(prior_runs),
        )
        graph_input = {
            "messages": [
                SystemMessage(content=built.system_prompt),
                _build_human_message(
                    input_text=payload.input,
                    image_refs=payload.image_refs,
                    supports_vision=built.supports_vision,
                ),
            ],
            "step_count": 0,
            "max_steps": built.max_steps,
        }
        configurable: dict[str, Any] = {
            "thread_id": str(thread_id),
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
        }
        if caller_user_id is not None:
            configurable["user_id"] = str(caller_user_id)
            # Stream J.3 — carry the user scope into the run worker's
            # context so the long-term-memory store's user-level RLS
            # applies. The background task inherits this ContextVar at
            # creation, exactly as it inherits the tenant id.
            current_user_id_var.set(caller_user_id)
        # Mini-ADR J-40 — when the manifest declares
        # ``policies.run_deadline_s > 0`` the worker pins a wall-clock
        # absolute deadline on config; SubAgentTool propagates it to
        # every child config unchanged so the whole delegation tree
        # honours the single budget.
        if built.run_deadline_s > 0:
            configurable["deadline_at"] = time.monotonic() + float(built.run_deadline_s)
        config: RunnableConfig = {"configurable": configurable}
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
                audit_logger=audit,
                approval_store=approvals,
            )
        )
        await runtime.run_manager.attach_task(run_id, worker)
        # Log only ``run_id`` — it is server-generated (``uuid4()``) and
        # uniquely identifies the run. ``thread_id`` (a request path
        # param) and the agent name / version (user-supplied) are
        # request-derived; CodeQL py/log-injection taints them, so they
        # are kept out of the log. Recover them from the run record.
        logger.info("control_plane.run.started run_id=%s", run_id)

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

    @router.get("/{thread_id}/runs/{run_id}", response_model=None)
    async def get_run(
        thread_id: UUID,
        run_id: UUID,
        request: Request,
        threads: Annotated[object, Depends(_get_thread_repo)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        approvals: Annotated[ApprovalStore, Depends(_get_approval_store)],
    ) -> JSONResponse:
        """Stream J.8 — a run's status + any pending approval.

        Reads the durable ``agent_approval`` row so a paused run is
        visible even after a control-plane restart (the in-memory
        RunManager would have lost it). 404 hides cross-tenant /
        cross-user existence, identical to ``trigger_run``.
        """
        tenant_id: UUID = request.state.tenant_id
        meta = await threads.get(thread_id, tenant_id=tenant_id)  # type: ignore[attr-defined]
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        caller_user_id = await resolve_caller_user_id(request, users)
        if not caller_owns_thread(
            meta=meta, caller_user_id=caller_user_id, principal=request.state.principal
        ):
            raise HTTPException(status_code=404, detail="session not found")

        approval = await approvals.get_by_run(run_id=run_id, tenant_id=tenant_id)
        pending: dict[str, Any] | None = None
        if approval is not None and approval.status is ApprovalStatus.PENDING:
            pending = {
                "request_id": approval.request_id,
                "node": approval.node,
                "reason_kind": approval.reason_kind,
                "action_summary": approval.action_summary,
                "proposed_args": approval.proposed_args,
                "requested_at": approval.requested_at.isoformat(),
                "timeout_at": approval.timeout_at.isoformat(),
            }
        # Status: the in-memory RunManager is authoritative while the run
        # is live; after a restart the agent_approval row carries it.
        run_record = runtime_run_status(request, run_id)
        if run_record is None and approval is None:
            raise HTTPException(status_code=404, detail="run not found")
        status = run_record or (approval.status.value if approval is not None else "unknown")
        return JSONResponse(
            content={
                "run_id": str(run_id),
                "thread_id": str(thread_id),
                "status": status,
                "pending_approval": pending,
            }
        )

    return router


def runtime_run_status(request: Request, run_id: UUID) -> str | None:
    """Return the in-memory RunManager's status string for ``run_id``.

    ``None`` when the run is unknown to this process — either it never
    ran here or the control-plane restarted (RunManager is in-memory;
    Mini-ADR J-24 — the ``agent_approval`` row is the durable fallback).
    """
    runtime: AgentRuntime = request.app.state.agent_runtime
    record = runtime.run_manager.get(run_id)
    return record.status.value if record is not None else None
