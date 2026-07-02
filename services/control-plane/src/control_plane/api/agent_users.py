"""``GET /v1/agents/{name}/{version}/users`` — the per-agent users rollup.

Conversation-centric IA M2 (``docs/design/conversation-centric-ia.md`` §5):
the "users" tab of an agent lists every end-user with ≥1 conversation on
that agent, with their conversation / run rollup and token totals — the
top of the user → conversation → run drill-down.

Composition, not a new store aggregate: ``agent_run`` has no agent
column (the agent dimension lives on ``thread_meta``), so this endpoint
folds ``thread_meta.list_by_tenant`` (agent filter) through the existing
``RunStore.aggregate_by_threads`` per-user, then joins display names from
``tenant_user`` and token totals from ``token_usage`` (which carries
``agent_name`` / ``agent_version`` / ``user_id`` directly — no trace
join). The thread window is capped at ``MAX_LIST_LIMIT`` like the
conversations list; a capped read is flagged via ``X-Limit-Capped``.

Tenant-scoped like the conversations detail: an agent's operations view
lives inside one tenant, so the cross-tenant ``"*"`` scope is rejected
(a system_admin passes a concrete ``tenant_id`` to drill in).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from control_plane.api._user_scope import get_user_repo, resolve_target_user_id
from control_plane.audit import emit
from control_plane.tenant_scope import (
    CrossTenant,
    SingleTenant,
    applied_scope,
    cross_tenant_query_enabled,
    ensure_tenant_scope,
)
from helix_agent.common.observability import current_trace_id_hex
from helix_agent.persistence.tenant_user.base import TenantUserStore
from helix_agent.persistence.thread_meta import ThreadMetaStore
from helix_agent.persistence.token_usage_store import TokenTotals, TokenUsageStore
from helix_agent.protocol import AuditAction, AuditResult, TenantUser
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.runs import RunStore
from helix_agent.runtime.runs.store import MAX_LIST_LIMIT


def _get_thread_repo(request: Request) -> ThreadMetaStore:
    return request.app.state.thread_meta_repo  # type: ignore[no-any-return]


def _get_run_store(request: Request) -> RunStore:
    return request.app.state.run_store  # type: ignore[no-any-return]


def _get_token_usage_store(request: Request) -> TokenUsageStore:
    return request.app.state.token_usage_store  # type: ignore[no-any-return]


def _get_audit(request: Request) -> AuditLogger:
    return request.app.state.audit_logger  # type: ignore[no-any-return]


@dataclass
class _UserFold:
    """Mutable per-user accumulator for the thread → user fold."""

    conversation_count: int = 0
    run_count: int = 0
    error_count: int = 0
    pending_count: int = 0
    last_run_at: datetime | None = field(default=None)


def _tokens_to_dict(t: TokenTotals) -> dict[str, Any]:
    return {
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "cache_creation_tokens": t.cache_creation_tokens,
        "cache_read_tokens": t.cache_read_tokens,
        "total_tokens": t.total_tokens,
        "llm_calls": t.llm_calls,
        "models": list(t.models),
    }


def _user_to_dict(
    user_id: UUID,
    fold: _UserFold,
    user: TenantUser | None,
    tokens: TokenTotals | None,
) -> dict[str, Any]:
    return {
        "user_id": str(user_id),
        "display_name": user.display_name if user is not None else None,
        "conversation_count": fold.conversation_count,
        "run_count": fold.run_count,
        "error_count": fold.error_count,
        "pending_count": fold.pending_count,
        "last_run_at": fold.last_run_at.isoformat() if fold.last_run_at is not None else None,
        "tokens": _tokens_to_dict(tokens) if tokens is not None else None,
    }


def build_agent_users_router() -> APIRouter:
    """Mount ``GET /v1/agents/{name}/{version}/users``."""
    router = APIRouter(prefix="/v1/agents", tags=["agents"])

    @router.get("/{agent_name}/{agent_version}/users", response_model=None)
    async def list_agent_users(
        agent_name: str,
        agent_version: str,
        request: Request,
        threads: Annotated[ThreadMetaStore, Depends(_get_thread_repo)],
        runs: Annotated[RunStore, Depends(_get_run_store)],
        token_usage: Annotated[TokenUsageStore, Depends(_get_token_usage_store)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        # An agent's users view lives inside one tenant — a concrete id lets
        # a system_admin drill in; the "*" scope is rejected below.
        tenant_id: Annotated[UUID | None, Query()] = None,
    ) -> JSONResponse:
        trace_id = current_trace_id_hex()
        start = time.monotonic()

        scope = await ensure_tenant_scope(
            request.state.principal,
            tenant_id,
            audit,
            trace_id=trace_id,
            endpoint="GET /v1/agents/{name}/{version}/users",
            cross_tenant_enabled=cross_tenant_query_enabled(request),
        )
        if isinstance(scope, CrossTenant):
            raise HTTPException(
                status_code=422,
                detail="an agent's users view is per-tenant; pass a concrete tenant_id",
            )
        target = scope.tenant_id

        async with applied_scope(SingleTenant(tenant_id=target)):
            # Thread window — same cap semantics as the conversations list.
            # A tenant+agent with more than MAX_LIST_LIMIT non-empty threads
            # gets a partial rollup, flagged via X-Limit-Capped.
            metas = await threads.list_by_tenant(
                target,
                agent_name=agent_name,
                agent_version=agent_version,
                nonempty=True,
                limit=MAX_LIST_LIMIT,
                offset=0,
            )
            thread_capped = len(metas) >= MAX_LIST_LIMIT

            owner_by_thread = {m.thread_id: m.user_id for m in metas if m.user_id is not None}
            aggs = (
                await runs.aggregate_by_threads(thread_ids=list(owner_by_thread), tenant_id=target)
                if owner_by_thread
                else {}
            )

            folds: dict[UUID, _UserFold] = {}
            for thread_id, agg in aggs.items():
                owner = owner_by_thread.get(thread_id)
                if owner is None:
                    continue
                fold = folds.setdefault(owner, _UserFold())
                fold.conversation_count += 1
                fold.run_count += agg.run_count
                fold.error_count += agg.error_count
                fold.pending_count += agg.pending_count
                if agg.last_run_at is not None and (
                    fold.last_run_at is None or agg.last_run_at > fold.last_run_at
                ):
                    fold.last_run_at = agg.last_run_at

            user_ids = list(folds)
            names = await users.get_many(user_ids, tenant_id=target) if user_ids else {}
            totals = (
                await token_usage.totals_by_users(
                    agent_name=agent_name,
                    agent_version=agent_version,
                    user_ids=user_ids,
                )
                if user_ids
                else {}
            )

        # Most-recently-active first — the operator's "who used this agent"
        # question is recency-shaped.
        ordered = sorted(
            folds.items(),
            key=lambda kv: (
                kv[1].last_run_at.timestamp() if kv[1].last_run_at is not None else float("-inf")
            ),
            reverse=True,
        )
        page = ordered[offset : offset + limit]
        items = [_user_to_dict(uid, fold, names.get(uid), totals.get(uid)) for uid, fold in page]

        await emit(
            audit,
            tenant_id=request.state.tenant_id,
            actor_id=request.state.actor_id,
            action=AuditAction.RUN_LIST_READ,
            resource_type="run",
            resource_id=None,
            result=AuditResult.SUCCESS,
            trace_id=trace_id,
            details={
                "view": "agent_users",
                "agent_name": agent_name,
                "agent_version": agent_version,
                "count": len(items),
                "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
            },
        )

        headers = {"X-Limit-Capped": "true"} if thread_capped else None
        return JSONResponse(
            content={
                "success": True,
                "data": {"items": items, "total": len(folds), "cross_tenant": False},
                "error": None,
            },
            headers=headers,
        )

    return router


def build_tenant_users_router() -> APIRouter:
    """Mount ``GET /v1/users/{user_id}`` — one registry row.

    Conversation-centric IA fast-follow: the user-detail page needs the
    member's ``display_name`` on a direct URL open (it previously only
    rode the Users-tab navigation state). Same per-user gate as the
    governance filters — the caller reads themself, a tenant admin reads
    any member (``resolve_target_user_id``).
    """
    router = APIRouter(prefix="/v1/users", tags=["users"])

    @router.get("/{user_id}", response_model=None)
    async def get_tenant_user(
        user_id: UUID,
        request: Request,
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        # A registry row belongs to one tenant — a concrete id lets a
        # system_admin drill in; the "*" scope is rejected below.
        tenant_id: Annotated[UUID | None, Query()] = None,
    ) -> JSONResponse:
        scope = await ensure_tenant_scope(
            request.state.principal,
            tenant_id,
            audit,
            trace_id=current_trace_id_hex(),
            endpoint="GET /v1/users/{user_id}",
            cross_tenant_enabled=cross_tenant_query_enabled(request),
        )
        if isinstance(scope, CrossTenant):
            raise HTTPException(
                status_code=422,
                detail="a user belongs to one tenant; pass a concrete tenant_id",
            )
        target = scope.tenant_id

        # Self-or-admin gate (403 for a plain member asking about someone
        # else); the actual read hides cross-tenant existence behind 404.
        await resolve_target_user_id(request, users, requested=user_id)
        user = await users.get(user_id, tenant_id=target)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")

        return JSONResponse(
            content={
                "success": True,
                "data": {
                    "user_id": str(user.id),
                    "display_name": user.display_name,
                    "subject_type": user.subject_type,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "last_active_at": (
                        user.last_active_at.isoformat() if user.last_active_at else None
                    ),
                },
                "error": None,
            }
        )

    return router
