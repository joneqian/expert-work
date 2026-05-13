"""``/v1/sessions`` CRUD + lifecycle — Stream B.6.

Owns the thin HTTP surface around :class:`ThreadMetaStore` (A.7) that
implements the durable-execution state machine from subsystems/19
§ 3.1. The orchestrator (Stream E) will consume the same rows; this
endpoint is what tenants drive directly.

State transitions enforced:

* create     → ``ACTIVE``
* ``ACTIVE`` → ``PAUSED`` (pause) / ``CANCELLED`` (cancel)
* ``PAUSED`` → ``ACTIVE`` (resume) / ``CANCELLED`` (cancel)
* terminal (``COMPLETED`` / ``FAILED`` / ``CANCELLED``): all transitions
  rejected with ``HTTP 409``

Same exception-to-response policy as ``/v1/agents``: ``str(exc)`` is
never echoed; the public message is a fixed sentence and the cause is
logged server-side.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from control_plane.api._quota_admission import check_admission
from control_plane.audit import emit
from control_plane.quota.base import QuotaService
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.persistence.thread_meta import ThreadMetaStore
from helix_agent.protocol import AgentSpecStatus, AuditAction, AuditResult, ThreadStatus
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.sessions")


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class CreateSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)


class TransitionPayload(BaseModel):
    """Body shared by ``pause`` / ``cancel`` — ``reason`` is operator-facing."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=512)


# ---------------------------------------------------------------------------
# Dependency providers (pull from request.app.state)
# ---------------------------------------------------------------------------


def _get_thread_repo(request: Request) -> ThreadMetaStore:
    return request.app.state.thread_meta_repo  # type: ignore[no-any-return]


def _get_agent_repo(request: Request) -> AgentSpecStore:
    return request.app.state.agent_spec_repo  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_quota(request: Request) -> QuotaService:
    return request.app.state.quota_service  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _envelope_error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "error": {"code": code, "message": message},
        },
    )


def _conflict(message: str) -> JSONResponse:
    return _envelope_error("SESSION_STATE_CONFLICT", message, 409)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def build_sessions_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

    @router.post("", status_code=201)
    async def create_session(
        payload: CreateSessionPayload,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        agents: Annotated[AgentSpecStore, Depends(_get_agent_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        quota: Annotated[QuotaService, Depends(_get_quota)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = request.state.actor_id
        trace_id = current_trace_id_hex()

        # Admission (Stream C.5b): consume one token from the tenant's
        # QPS bucket before doing any other work. Denial emits a
        # ``quota:rate_limit_denied`` audit row and returns 429 with
        # ``Retry-After``; we never proceed to DB writes.
        denial = await check_admission(
            quota=quota,
            audit=audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            agent=payload.agent_name,
            resource_kind="session",
        )
        if denial is not None:
            return denial

        # The agent must exist + be ACTIVE (not deprecated / soft-deleted)
        # for the tenant. Otherwise the session would point at a row a
        # later GET would fail to resolve.
        record = await agents.get(
            tenant_id=tenant_id,
            name=payload.agent_name,
            version=payload.agent_version,
        )
        if record is None or record.status is not AgentSpecStatus.ACTIVE:
            await emit(
                audit,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=AuditAction.SESSION_WRITE,
                resource_type="session",
                resource_id=f"{payload.agent_name}/{payload.agent_version}",
                result=AuditResult.ERROR,
                reason="agent_not_found",
                trace_id=trace_id,
            )
            return _envelope_error(
                "AGENT_NOT_FOUND",
                "agent does not exist or is not active for this tenant",
                422,
            )

        thread_id = uuid4()
        meta = await threads.create(
            thread_id=thread_id,
            tenant_id=tenant_id,
            created_by=actor_id,
            agent_name=payload.agent_name,
            agent_version=payload.agent_version,
        )
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SESSION_WRITE,
            resource_type="session",
            resource_id=str(thread_id),
            trace_id=trace_id,
            details={"agent": f"{payload.agent_name}/{payload.agent_version}"},
        )
        return JSONResponse(
            status_code=201,
            content={"success": True, "data": meta.model_dump(mode="json")},
        )

    @router.get("/{thread_id}")
    async def get_session(
        thread_id: UUID,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        meta = await threads.get(thread_id, tenant_id=tenant_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=request.state.actor_id,
            action=AuditAction.SESSION_READ,
            resource_type="session",
            resource_id=str(thread_id),
            trace_id=current_trace_id_hex(),
        )
        return JSONResponse({"success": True, "data": meta.model_dump(mode="json")})

    @router.get("")
    async def list_sessions(
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        status: ThreadStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        items = await threads.list_by_tenant(tenant_id, status=status, limit=limit, offset=offset)
        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=request.state.actor_id,
            action=AuditAction.SESSION_READ,
            resource_type="session",
            trace_id=current_trace_id_hex(),
            details={"count": len(items)},
        )
        return JSONResponse(
            {
                "success": True,
                "data": {
                    "items": [m.model_dump(mode="json") for m in items],
                    "total": len(items),
                },
            }
        )

    async def _transition(
        *,
        thread_id: UUID,
        request: Request,
        threads: ThreadMetaStore,
        audit: AuditLogger,
        target: ThreadStatus,
        allowed_from: frozenset[ThreadStatus],
        audit_action: AuditAction,
        reason: str | None,
    ) -> JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = request.state.actor_id
        trace_id = current_trace_id_hex()

        meta = await threads.get(thread_id, tenant_id=tenant_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")

        if meta.status not in allowed_from:
            await emit(
                audit,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=audit_action,
                resource_type="session",
                resource_id=str(thread_id),
                result=AuditResult.ERROR,
                reason=f"illegal_transition_from_{meta.status.value}",
                trace_id=trace_id,
            )
            return _conflict(f"cannot transition from {meta.status.value} to {target.value}")

        updated = await threads.update_status(thread_id, target, tenant_id=tenant_id)
        if not updated:
            # The row vanished between get + update — treat as 404 for tenant safety.
            raise HTTPException(status_code=404, detail="session not found")

        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=audit_action,
            resource_type="session",
            resource_id=str(thread_id),
            trace_id=trace_id,
            details={"to": target.value, "reason": reason} if reason else {"to": target.value},
        )
        # Re-fetch so the response reflects the row after the update.
        fresh = await threads.get(thread_id, tenant_id=tenant_id)
        if fresh is None:
            raise HTTPException(status_code=404, detail="session not found")
        return JSONResponse({"success": True, "data": fresh.model_dump(mode="json")})

    @router.post("/{thread_id}:pause")
    async def pause_session(
        thread_id: UUID,
        payload: TransitionPayload,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        return await _transition(
            thread_id=thread_id,
            request=request,
            threads=threads,
            audit=audit,
            target=ThreadStatus.PAUSED,
            allowed_from=frozenset({ThreadStatus.ACTIVE}),
            audit_action=AuditAction.SESSION_WRITE,
            reason=payload.reason,
        )

    @router.post("/{thread_id}:resume")
    async def resume_session(
        thread_id: UUID,
        payload: TransitionPayload,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        return await _transition(
            thread_id=thread_id,
            request=request,
            threads=threads,
            audit=audit,
            target=ThreadStatus.ACTIVE,
            allowed_from=frozenset({ThreadStatus.PAUSED}),
            audit_action=AuditAction.SESSION_WRITE,
            reason=payload.reason,
        )

    @router.post("/{thread_id}:cancel")
    async def cancel_session(
        thread_id: UUID,
        payload: TransitionPayload,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
    ) -> JSONResponse:
        return await _transition(
            thread_id=thread_id,
            request=request,
            threads=threads,
            audit=audit,
            target=ThreadStatus.CANCELLED,
            allowed_from=frozenset({ThreadStatus.ACTIVE, ThreadStatus.PAUSED}),
            audit_action=AuditAction.SESSION_CANCEL,
            reason=payload.reason,
        )

    return router
