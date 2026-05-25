"""Cross-tenant scope resolution — Stream N (Mini-ADR N-3, N-4, N-5).

A single, central decision point that every list/detail endpoint **must**
go through to resolve "which tenant(s) does this request operate on?"
from the route's ``tenant_id`` query parameter and the verified
:class:`Principal`.

The function returns one of two resolutions:

* :class:`SingleTenant` — the request runs against exactly one tenant
  (normal RLS path; the ``app.tenant_id`` GUC is set by the existing
  ``RLSContextMiddleware``).
* :class:`CrossTenant` — the request runs across **all** tenants
  (``bypass_rls_var=True``). Only available when
  ``principal.is_system_admin is True`` (Stream N — Mini-ADR N-3).

Both resolutions emit an audit row when warranted:

* ``tenant_id="*"`` query → ``AuditAction.SYSTEM_CROSS_TENANT_QUERY``
* ``tenant_id`` ≠ ``principal.tenant_id`` (and the caller is system_admin)
  → ``AuditAction.SYSTEM_TENANT_SWITCH``

The companion :func:`bypass_rls_session` async-context-manager wraps a
SQL store call so the ``after_begin`` RLS listener skips emitting
``set_config('app.tenant_id', ...)``. Use only inside endpoints that
have just resolved to :class:`CrossTenant`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from control_plane.audit import emit
from helix_agent.persistence.rls import bypass_rls_var, current_tenant_id_var
from helix_agent.protocol import AuditAction, Principal
from helix_agent.runtime.audit.logger import AuditLogger

logger = logging.getLogger("helix.control_plane.tenant_scope")


# ---------------------------------------------------------------------------
# Resolution types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SingleTenant:
    """The request operates on exactly one tenant (normal RLS)."""

    tenant_id: UUID


@dataclass(frozen=True)
class CrossTenant:
    """The request runs across all tenants (RLS bypassed, system_admin only)."""


TenantScopeResolution = SingleTenant | CrossTenant


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


async def ensure_tenant_scope(
    principal: Principal,
    requested_tenant_id: UUID | Literal["*"] | None,
    audit: AuditLogger,
    *,
    trace_id: str | None = None,
    endpoint: str | None = None,
) -> TenantScopeResolution:
    """Resolve ``?tenant_id=`` against the caller's scope.

    Decision matrix:

    ====================  =================  =====================================
    requested_tenant_id   principal status   result
    ====================  =================  =====================================
    ``"*"``               system_admin       :class:`CrossTenant` + audit
    ``"*"``               NOT system_admin   403 ``CROSS_TENANT_FORBIDDEN``
    UUID = home tenant    any                :class:`SingleTenant`
    UUID = other tenant   in allowed_tenants :class:`SingleTenant` (+switch audit for sysadmin)
    UUID = other tenant   NOT allowed        403 ``TENANT_NOT_ALLOWED``
    None                  any                :class:`SingleTenant` (home tenant)
    ====================  =================  =====================================

    When ``CrossTenant`` is returned, the caller MUST wrap the SQL
    query in :func:`bypass_rls_session`; the resolver only decides
    *whether* bypass is allowed, not when to flip the ContextVar.

    All ``"*"`` queries and all explicit tenant switches (where the
    target differs from the principal's home tenant) emit an audit row.
    """
    # --- cross-tenant aggregate path ---------------------------------
    if requested_tenant_id == "*":
        if not principal.is_system_admin:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "CROSS_TENANT_FORBIDDEN",
                    "message": "cross-tenant query (tenant_id=*) requires system_admin",
                },
            )
        await emit(
            audit,
            tenant_id=principal.tenant_id,  # home tenant — audit attribution
            actor_id=principal.subject_id,
            action=AuditAction.SYSTEM_CROSS_TENANT_QUERY,
            resource_type="system",
            resource_id=endpoint,
            trace_id=trace_id,
            details={"endpoint": endpoint} if endpoint else {},
        )
        logger.info(
            "tenant_scope.cross_tenant",
            extra={"actor_id": principal.subject_id, "endpoint": endpoint},
        )
        return CrossTenant()

    # --- single-tenant path ------------------------------------------
    target: UUID = requested_tenant_id if requested_tenant_id is not None else principal.tenant_id

    # principal.allowed_tenants == "*" ⇔ system_admin
    if principal.allowed_tenants != "*" and target not in principal.allowed_tenants:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TENANT_NOT_ALLOWED",
                "message": "the caller is not authorized for this tenant",
            },
        )

    # Tenant switch audit — system_admin (or mTLS) operating on a tenant
    # other than their home tenant. Skipped for normal tenant users
    # whose target always equals their home tenant (already enforced
    # above; the inequality is impossible for them without a 403).
    if target != principal.tenant_id and principal.is_system_admin:
        await emit(
            audit,
            tenant_id=target,  # action recorded under the target tenant
            actor_id=principal.subject_id,
            action=AuditAction.SYSTEM_TENANT_SWITCH,
            resource_type="system",
            resource_id=endpoint,
            trace_id=trace_id,
            details={
                "endpoint": endpoint,
                "home_tenant": str(principal.tenant_id),
            }
            if endpoint
            else {"home_tenant": str(principal.tenant_id)},
        )
        logger.info(
            "tenant_scope.tenant_switch",
            extra={
                "actor_id": principal.subject_id,
                "home_tenant": str(principal.tenant_id),
                "target_tenant": str(target),
                "endpoint": endpoint,
            },
        )

    return SingleTenant(tenant_id=target)


# ---------------------------------------------------------------------------
# bypass_rls_session — CrossTenant SQL wrapper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def bypass_rls_session() -> AsyncIterator[None]:
    """Async context manager flipping ``bypass_rls_var=True`` for the body.

    Matches the existing per-worker bypass pattern
    (``CurationWorker._bypass_rls`` / ``Scheduler._bypass_rls``) but
    exposed as a public helper for HTTP endpoints that have just
    resolved to :class:`CrossTenant`. Resets ContextVar on exit so
    nested calls inherit cleanly.
    """
    bypass = bypass_rls_var.set(True)
    tenant = current_tenant_id_var.set(None)
    try:
        yield
    finally:
        current_tenant_id_var.reset(tenant)
        bypass_rls_var.reset(bypass)


@asynccontextmanager
async def applied_scope(scope: TenantScopeResolution) -> AsyncIterator[None]:
    """Apply a :class:`TenantScopeResolution` to the SQL session — Stream N.

    * :class:`CrossTenant` → ``bypass_rls_var=True`` + ``current_tenant_id_var=None``;
      the SQL store's own WHERE clauses (or ``list_all_tenants`` methods) decide
      what gets returned.
    * :class:`SingleTenant` → ``bypass_rls_var=False`` + ``current_tenant_id_var=scope.tenant_id``;
      this **rebinds** the GUC away from the request-middleware default
      (``principal.tenant_id``) so a system_admin's tenant-switch query
      filters at the RLS layer instead of relying purely on the store's
      ``WHERE tenant_id = ?`` clause (defense in depth).

    Endpoints should wrap their SQL store call inside this manager:

    .. code:: python

        scope = await ensure_tenant_scope(principal, tenant_id, audit, ...)
        async with applied_scope(scope):
            if isinstance(scope, CrossTenant):
                rows = await store.list_all_tenants(...)
            else:
                rows = await store.list_by_tenant(tenant_id=scope.tenant_id, ...)
    """
    if isinstance(scope, CrossTenant):
        b = bypass_rls_var.set(True)
        t = current_tenant_id_var.set(None)
        try:
            yield
        finally:
            current_tenant_id_var.reset(t)
            bypass_rls_var.reset(b)
    else:
        b = bypass_rls_var.set(False)
        t = current_tenant_id_var.set(scope.tenant_id)
        try:
            yield
        finally:
            current_tenant_id_var.reset(t)
            bypass_rls_var.reset(b)


__all__ = [
    "CrossTenant",
    "SingleTenant",
    "TenantScopeResolution",
    "applied_scope",
    "bypass_rls_session",
    "ensure_tenant_scope",
]
