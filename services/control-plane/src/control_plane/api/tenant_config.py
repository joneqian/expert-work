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


def _embedding_provider(request: Request) -> Provider:
    """Stream O Mini-ADR O-12 — the platform embedding provider whose
    credential long-term memory needs. Read from settings on app.state;
    test apps that don't wire settings fall back to the ``qwen`` default
    (matching :class:`Settings.embedding_provider`)."""
    settings = getattr(request.app.state, "settings", None)
    provider: Provider = getattr(settings, "embedding_provider", "qwen")
    return provider


def _credentials_catalog(
    request: Request,
) -> tuple[list[Provider], dict[Provider, str], list[Tool], dict[Tool, str]]:
    """Stream O Mini-ADR O-13 — the effective platform catalog the
    Credentials panel renders rows from. Read from settings on app.state;
    test apps that don't wire settings yield empty catalogs."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return [], {}, [], {}
    return (
        settings.effective_supported_providers,
        settings.effective_platform_provider_credentials,
        settings.effective_supported_tools,
        settings.effective_platform_tool_credentials,
    )


def _providers_referenced_by(
    record: AgentSpecRecord, *, embedding_provider: Provider
) -> set[Provider]:
    """Providers a single agent transitively references — primary model +
    its fallback chain, vision model + fallbacks, and
    ``memory_consolidation.aux_model``.

    Mini-ADR O-12 — long-term memory uses the platform ``embedding_provider``
    (a platform-infra provider, not declared in any manifest model field), so
    an agent declaring ``memory.long_term`` references it too. Rerank is NOT
    included — a missing rerank credential degrades gracefully (Mini-ADR O-9)."""
    agent_spec = record.spec
    used: set[Provider] = set()
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
    memory = agent_spec.spec.memory
    if memory is not None and memory.long_term is not None:
        used.add(embedding_provider)
    return used


def _tools_referenced_by(record: AgentSpecRecord) -> set[Tool]:
    """External SaaS tools a single agent references. ``web_search`` is the
    only :data:`TOOL_CATALOG` entry today; other tool names (filesystem /
    exec_python / MCP) don't consume Stream O credentials."""
    used: set[Tool] = set()
    for entry in record.spec.spec.tools:
        tool_name = getattr(entry, "name", None) or getattr(entry, "tool", None)
        if tool_name == "web_search":
            used.add("web_search")
    return used


def _collect_used_providers(
    specs: Iterable[AgentSpecRecord], *, embedding_provider: Provider
) -> set[Provider]:
    """Stream O Mini-ADR O-4 — union of every provider referenced by any
    agent in the tenant (see :func:`_providers_referenced_by`)."""
    used: set[Provider] = set()
    for record in specs:
        used |= _providers_referenced_by(record, embedding_provider=embedding_provider)
    return used


def _collect_used_tools(specs: Iterable[AgentSpecRecord]) -> set[Tool]:
    """Stream O — union of every external tool referenced by any agent."""
    used: set[Tool] = set()
    for record in specs:
        used |= _tools_referenced_by(record)
    return used


def _provider_usage_counts(
    specs: Iterable[AgentSpecRecord], *, embedding_provider: Provider
) -> dict[Provider, int]:
    """Stream O Mini-ADR O-13 — per-provider count of agents referencing it
    (drives the Credentials panel's ``used_by_agents`` column)."""
    counts: dict[Provider, int] = {}
    for record in specs:
        for provider in _providers_referenced_by(record, embedding_provider=embedding_provider):
            counts[provider] = counts.get(provider, 0) + 1
    return counts


def _tool_usage_counts(specs: Iterable[AgentSpecRecord]) -> dict[Tool, int]:
    """Stream O Mini-ADR O-13 — per-tool count of agents referencing it."""
    counts: dict[Tool, int] = {}
    for record in specs:
        for tool in _tools_referenced_by(record):
            counts[tool] = counts.get(tool, 0) + 1
    return counts


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
                used_provs = _collect_used_providers(
                    specs, embedding_provider=_embedding_provider(request)
                )
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

    @router.get("/{tenant_id}/config/credentials")
    async def get_credentials_view(
        tenant_id: UUID,
        principal: Annotated[Principal, Depends(require("tenant_config", "read"))],
        svc: Annotated[TenantConfigService, Depends(_get_service)],
        request: Request,
    ) -> dict[str, object]:
        """Stream O Mini-ADR O-13 — composite view that drives the Admin UI
        Credentials panel: per catalog provider/tool, the platform-configured
        flag, the tenant secret_ref, the used-by-agents count, plus the current
        mode. No secret VALUES are returned — only refs (kms:// URIs) and
        booleans."""
        _ensure_tenant_match(principal, tenant_id)
        supported_provs, plat_provs, supported_tools, plat_tools = _credentials_catalog(request)
        try:
            record = await svc.get(tenant_id=tenant_id, actor_id=principal.subject_id)
            mode: str = record.credentials_mode
            tenant_provs = record.model_credentials_ref
            tenant_tools = record.tool_credentials
        except TenantConfigNotConfiguredError:
            mode, tenant_provs, tenant_tools = "platform", {}, {}
        embedding_provider = _embedding_provider(request)
        prov_counts: dict[Provider, int] = {}
        tool_counts: dict[Tool, int] = {}
        agent_store = _get_agent_spec_store(request)
        if agent_store is not None:
            specs = await agent_store.list_by_tenant(tenant_id=tenant_id, status=None, limit=1000)
            prov_counts = _provider_usage_counts(specs, embedding_provider=embedding_provider)
            tool_counts = _tool_usage_counts(specs)
        providers = [
            {
                "provider": provider,
                "platform_configured": provider in plat_provs,
                "tenant_secret_ref": tenant_provs.get(provider),
                "used_by_agents": prov_counts.get(provider, 0),
            }
            for provider in supported_provs
        ]
        tools = [
            {
                "tool": tool,
                "platform_configured": tool in plat_tools,
                "tenant_secret_ref": tenant_tools.get(tool),
                "used_by_agents": tool_counts.get(tool, 0),
            }
            for tool in supported_tools
        ]
        data = {"mode": mode, "providers": providers, "tools": tools}
        return {"success": True, "data": data, "error": None}

    @router.post("/{tenant_id}/config/credentials-mode/dry-run")
    async def dry_run_credentials_mode(
        tenant_id: UUID,
        payload: TenantConfigPatch,
        principal: Annotated[Principal, Depends(require("tenant_config", "read"))],
        svc: Annotated[TenantConfigService, Depends(_get_service)],
        request: Request,
    ) -> dict[str, object]:
        """Stream O Mini-ADR O-13 — preview switching to ``tenant`` mode
        WITHOUT persisting. ``payload`` carries the proposed tenant
        credentials (``model_credentials_ref`` / ``tool_credentials``); the
        response lists the providers/tools still missing a credential. The UI
        gates its "Switch to Tenant" button on this; ``PUT /config`` keeps the
        O-4 enforcement gate as the real backstop."""
        _ensure_tenant_match(principal, tenant_id)
        try:
            existing: TenantConfigRecord | None = await svc.get(
                tenant_id=tenant_id, actor_id=principal.subject_id
            )
        except TenantConfigNotConfiguredError:
            existing = None
        used_provs: set[Provider] = set()
        used_tools: set[Tool] = set()
        agent_store = _get_agent_spec_store(request)
        if agent_store is not None:
            specs = await agent_store.list_by_tenant(tenant_id=tenant_id, status=None, limit=1000)
            used_provs = _collect_used_providers(
                specs, embedding_provider=_embedding_provider(request)
            )
            used_tools = _collect_used_tools(specs)
        # Force the tenant-mode preview regardless of the body's mode field.
        preview = payload.model_copy(update={"credentials_mode": "tenant"})
        missing_p: list[Provider] = []
        missing_t: list[Tool] = []
        try:
            _validate_credentials_mode_switch(
                patch=preview,
                existing=existing,
                used_providers=used_provs,
                used_tools=used_tools,
            )
        except CredentialsModeSwitchIncompleteError as exc:
            missing_p = exc.missing_providers
            missing_t = exc.missing_tools
        data = {
            "ok": not (missing_p or missing_t),
            "missing_providers": missing_p,
            "missing_tools": missing_t,
        }
        return {"success": True, "data": data, "error": None}

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
