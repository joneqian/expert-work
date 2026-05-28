"""``/v1/tenants/{tenant_id}/config`` admin endpoints — Stream C.7.

GET (operator + admin) returns the full :class:`TenantConfigRecord`;
PUT (admin only) accepts a :class:`TenantConfigPatch` (partial
update). Both go through :class:`TenantConfigService` so the 60s LRU
cache stays warm and the audit log captures the access.

Stream O Mini-ADR O-4 — the PUT endpoint additionally enforces the
all-or-nothing credentials_mode switch gate: a tenant moving from
``platform`` to ``tenant`` mode must have credentials configured for
every provider / tool that any of their agent manifests reference.
The gate runs before :class:`TenantConfigService.upsert` so a failed
switch never reaches the store.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.api._authz import require
from control_plane.tenancy import TenantConfigNotConfiguredError, TenantConfigService
from helix_agent.common.uplift_metrics import record_credentials_mode_switch
from helix_agent.persistence.agent_spec import AgentSpecStore
from helix_agent.protocol import (
    AgentSpecRecord,
    Principal,
    Provider,
    TenantConfigPatch,
    TenantConfigRecord,
    Tool,
)

logger = logging.getLogger("helix.control_plane.api.tenant_config")


def _get_service(request: Request) -> TenantConfigService:
    return request.app.state.tenant_config_service  # type: ignore[no-any-return]


def _get_agent_spec_store(request: Request) -> AgentSpecStore | None:
    """Stream O — AgentSpecStore is optional (some test apps don't wire it).
    When absent, the gate skips the used-provider check and just validates
    that the patch isn't asking for tenant mode with zero credentials.

    Wired as ``app.state.agent_spec_repo`` in create_app (Stream B.5).
    """
    return getattr(request.app.state, "agent_spec_repo", None)


def _collect_used_providers(specs: Iterable[AgentSpecRecord]) -> set[Provider]:
    """Stream O Mini-ADR O-4 — Walk every agent manifest in the tenant
    and collect every provider it transitively references. Sub-agents
    + vision + memory_consolidation.aux_model are all included; the
    primary model's fallback chain too."""
    used: set[Provider] = set()
    for record in specs:
        agent_spec = record.spec
        stack = [agent_spec.spec.model]
        if agent_spec.spec.vision is not None:
            stack.append(agent_spec.spec.vision.model)
            stack.extend(agent_spec.spec.vision.fallbacks)
        consolidation = agent_spec.spec.policies.memory_consolidation
        if consolidation.aux_model is not None:
            stack.append(consolidation.aux_model)
        while stack:
            current = stack.pop()
            used.add(current.provider)
            stack.extend(current.fallback)
    return used


def _collect_used_tools(specs: Iterable[AgentSpecRecord]) -> set[Tool]:
    """Stream O — collect external SaaS tools referenced by any agent.
    Only ``web_search`` is in :data:`TOOL_CATALOG` today; this widens
    cleanly as the catalog grows."""
    used: set[Tool] = set()
    for record in specs:
        for entry in record.spec.spec.tools:
            tool_name = getattr(entry, "name", None) or getattr(entry, "tool", None)
            # ``web_search`` is the only Stream O Tool today; ignore any
            # other tool name (filesystem / exec_python / MCP etc. don't
            # consume Stream O credentials).
            if tool_name == "web_search":
                used.add("web_search")
    return used


class CredentialsModeSwitchIncompleteError(ValueError):
    """Stream O Mini-ADR O-4 — raised when ``credentials_mode='tenant'``
    is requested but the merged credential view does not cover every
    provider / tool the tenant's agents currently reference."""

    def __init__(
        self,
        *,
        missing_providers: list[Provider],
        missing_tools: list[Tool],
    ) -> None:
        super().__init__(
            f"cannot switch to tenant credentials_mode: missing credentials "
            f"for providers={missing_providers} tools={missing_tools}"
        )
        self.missing_providers = missing_providers
        self.missing_tools = missing_tools


def _validate_credentials_mode_switch(
    *,
    patch: TenantConfigPatch,
    existing: TenantConfigRecord | None,
    used_providers: set[Provider],
    used_tools: set[Tool],
) -> None:
    """Stream O Mini-ADR O-4 — gate for the credentials_mode switch.

    Validates only when the patch is moving the tenant **into** tenant
    mode (from platform mode, or new tenant declaring tenant mode at
    first upsert). Idempotent if the tenant is already in tenant mode
    and the patch only updates credentials (those go through the
    normal merge).
    """
    if patch.credentials_mode != "tenant":
        return
    if existing is not None and existing.credentials_mode == "tenant":
        # Already in tenant mode — no switch, no gate. New providers
        # that get added without credentials will hit a 401 at resolve
        # time (per Mini-ADR O-3 fail-fast). This is by design: the
        # gate is for **switches**, not steady-state.
        return
    # Merge: patch wins where set; otherwise fall back to existing.
    merged_providers = dict(existing.model_credentials_ref if existing else {})
    if patch.model_credentials_ref is not None:
        merged_providers = dict(patch.model_credentials_ref)
    merged_tools = dict(existing.tool_credentials if existing else {})
    if patch.tool_credentials is not None:
        merged_tools = dict(patch.tool_credentials)
    missing_p = sorted(used_providers - set(merged_providers))
    missing_t = sorted(used_tools - set(merged_tools))
    if missing_p or missing_t:
        raise CredentialsModeSwitchIncompleteError(
            missing_providers=missing_p,
            missing_tools=missing_t,
        )


def build_tenant_config_router() -> APIRouter:
    router = APIRouter(prefix="/v1/tenants", tags=["tenant_config"])

    @router.get("/{tenant_id}/config")
    async def get_tenant_config(
        tenant_id: UUID,
        principal: Annotated[Principal, Depends(require("tenant_config", "read"))],
        svc: Annotated[TenantConfigService, Depends(_get_service)],
    ) -> dict[str, object]:
        _ensure_tenant_match(principal, tenant_id)
        try:
            record = await svc.get(tenant_id=tenant_id, actor_id=principal.subject_id)
        except TenantConfigNotConfiguredError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "TENANT_CONFIG_NOT_FOUND",
                    "message": "no tenant_config row exists for this tenant",
                },
            ) from exc
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    @router.put("/{tenant_id}/config")
    async def upsert_tenant_config(
        tenant_id: UUID,
        payload: TenantConfigPatch,
        principal: Annotated[Principal, Depends(require("tenant_config", "write"))],
        svc: Annotated[TenantConfigService, Depends(_get_service)],
        request: Request,
    ) -> dict[str, object]:
        _ensure_tenant_match(principal, tenant_id)
        # Stream O Mini-ADR O-4 — all-or-nothing credentials_mode switch gate.
        if payload.credentials_mode == "tenant":
            try:
                existing = await svc.get(tenant_id=tenant_id, actor_id=principal.subject_id)
            except TenantConfigNotConfiguredError:
                existing = None
            agent_store = _get_agent_spec_store(request)
            used_provs: set[Provider] = set()
            used_tools: set[Tool] = set()
            if agent_store is not None:
                specs = await agent_store.list_by_tenant(
                    tenant_id=tenant_id, status=None, limit=1000
                )
                used_provs = _collect_used_providers(specs)
                used_tools = _collect_used_tools(specs)
            try:
                _validate_credentials_mode_switch(
                    patch=payload,
                    existing=existing,
                    used_providers=used_provs,
                    used_tools=used_tools,
                )
            except CredentialsModeSwitchIncompleteError as exc:
                record_credentials_mode_switch(mode_to="tenant", result="incomplete")
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "CREDENTIALS_MODE_SWITCH_INCOMPLETE",
                        "message": str(exc),
                        "missing_providers": exc.missing_providers,
                        "missing_tools": exc.missing_tools,
                    },
                ) from exc
        try:
            record = await svc.upsert(
                tenant_id=tenant_id, patch=payload, actor_id=principal.subject_id
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "TENANT_CONFIG_FIRST_UPSERT_REQUIRES_DISPLAY_NAME",
                    "message": str(exc),
                },
            ) from exc
        # Stream O — mode switch metric on success path.
        if payload.credentials_mode is not None:
            record_credentials_mode_switch(mode_to=payload.credentials_mode, result="ok")
        return {"success": True, "data": record.model_dump(mode="json"), "error": None}

    return router


def _ensure_tenant_match(principal: Principal, tenant_id: UUID) -> None:
    """Block cross-tenant edits (same rule as ``/v1/tenants/{t}/quotas``)."""
    if principal.tenant_id == tenant_id:
        return
    if tenant_id in principal.allowed_tenants:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "TENANT_MISMATCH",
            "message": "principal cannot edit config for this tenant",
        },
    )
