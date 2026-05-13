"""``/v1/sessions/{thread_id}/runs`` SSE trigger — Stream B.7.

M0 ships a **fake stream**: three ``token`` events, an optional
``heartbeat``, and one terminal ``done``. Stream E replaces the inner
generator with the real LangGraph stream — the SSE event family
(``token`` / ``heartbeat`` / ``done``) is fixed by ADR B-4 so clients
written against this endpoint will work unchanged.

Cancellation: each iteration of the generator inspects
``request.state.cancel_token`` (set by :class:`CancellationMiddleware`).
A flipped token short-circuits with a ``done`` event whose ``reason``
is ``"cancelled"``. This is what closes verification gate #3 from the
client's perspective.

Audit: a single ``session:write`` row lands at the start of the run.
A second row is *not* written on stream close — that lifecycle event
will live with the orchestrator (Stream E) when the real generator
knows the actual outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from time import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from control_plane.api._quota_admission import check_admission
from control_plane.audit import emit
from control_plane.quota.base import QuotaService
from helix_agent.common.deadline import CancelToken
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.thread_meta import ThreadMetaStore
from helix_agent.protocol import AuditAction, ThreadStatus
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.runs")

#: Fixed M0 fake token sequence. Stream E supersedes this with the
#: actual LangGraph token stream.
_FAKE_TOKENS: tuple[str, ...] = (
    "Hello",
    " from",
    " Helix-Agent",
)

#: Inter-token delay used in production; tests set this to 0.0 via the
#: ``run_fake_token_delay_s`` setting hook.
_DEFAULT_TOKEN_DELAY_S = 0.005


class RunRequest(BaseModel):
    """POST body. ``input`` is the user's prompt; placeholder until E."""

    model_config = ConfigDict(extra="forbid")

    input: str | None = Field(default=None, max_length=8192)


def _get_thread_repo(request: Request) -> ThreadMetaStore:
    return request.app.state.thread_meta_repo  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


def _get_quota(request: Request) -> QuotaService:
    return request.app.state.quota_service  # type: ignore[no-any-return]


def _format_sse(event: str, data: dict[str, object]) -> bytes:
    """Render one SSE event in the spec-defined wire format."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _cancel_token_from(request: Request) -> CancelToken | None:
    return getattr(request.state, "cancel_token", None)


async def _fake_stream(
    *,
    thread_id: UUID,
    cancel_token: CancelToken | None,
    token_delay_s: float,
) -> AsyncIterator[bytes]:
    """Three tokens then a ``done`` event. Honours cancel mid-stream."""
    for seq, text in enumerate(_FAKE_TOKENS, start=1):
        if cancel_token is not None and cancel_token.cancelled:
            yield _format_sse("done", {"reason": "cancelled"})
            return
        yield _format_sse("token", {"seq": seq, "text": text})
        if token_delay_s > 0:
            await asyncio.sleep(token_delay_s)

    yield _format_sse(
        "done",
        {"reason": "fake_complete", "thread_id": str(thread_id), "ts": int(time() * 1000)},
    )


def build_runs_router() -> APIRouter:
    router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

    @router.post("/{thread_id}/runs", response_model=None)
    async def trigger_run(
        thread_id: UUID,
        payload: RunRequest,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        quota: Annotated[QuotaService, Depends(_get_quota)],
    ) -> StreamingResponse | JSONResponse:
        tenant_id: UUID = request.state.tenant_id
        actor_id: str = request.state.actor_id
        trace_id = current_trace_id_hex()

        meta = await threads.get(thread_id, tenant_id=tenant_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        if meta.status is not ThreadStatus.ACTIVE:
            raise HTTPException(
                status_code=409,
                detail=f"session is {meta.status.value}; only active sessions accept runs",
            )

        # Admission (Stream C.5b): bucket the run against the agent
        # bound to this thread. Denial returns 429 with ``Retry-After``
        # and audits — we never start the SSE stream.
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

        await emit(
            audit,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.SESSION_WRITE,
            resource_type="session",
            resource_id=str(thread_id),
            trace_id=trace_id,
            details={
                "stage": "run.start",
                "input_len": len(payload.input or ""),
            },
        )

        cancel_token = _cancel_token_from(request)
        token_delay_s = getattr(
            request.app.state.settings,
            "run_fake_token_delay_s",
            _DEFAULT_TOKEN_DELAY_S,
        )

        return StreamingResponse(
            _fake_stream(
                thread_id=thread_id,
                cancel_token=cancel_token,
                token_delay_s=token_delay_s,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
