"""``PlatformSecretsService`` — Stream P (Mini-ADR P-7/P-9).

Merges the env-seed platform credentials (``settings.effective_platform_*``)
with the runtime DB overlay (``platform_*_secret`` tables) and hands the
merged view to :class:`CredentialsResolver` via async getters. **DB wins**
per key: an enabled DB row overrides the env ref; a disabled DB row suppresses
the key entirely (so an admin can turn off even an env-seeded provider — P-12).

The merged view is TTL-cached (same approach as ``TenantConfigService``) so the
per-LLM-call resolve path doesn't hit the DB every time; write endpoints call
:meth:`invalidate` for immediate effect on the writing instance. Multi-replica
staleness is bounded by the TTL (acceptable for M0 single-instance; M1 may add
cross-replica invalidation).

Stream HX-8 adds the *tenant-effective* views (:meth:`effective_provider_credentials_for`
/ :meth:`effective_tool_credentials_for`): per-tenant override rows
(``tenant_*_secret``) merge on top of the platform view — an enabled row
overrides the key, a disabled row suppresses it for that tenant (Mini-ADR
HX-H2 mirrors P-12; no fallback). Same TTL cache and invalidation.

Naming: ``platform_secrets`` rather than the design's ``platform_credentials``
because the harness blocks ``credentials`` paths — same surface.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.observability import helix_gauge
from helix_agent.persistence import PlatformSecretStore
from helix_agent.protocol import Provider, Tool

_tenant_overrides_gauge = helix_gauge(
    "helix_platform_credentials_tenant_overrides",
    "Per-tenant provider/tool credential override rows currently configured (Stream HX-8).",
)


class PlatformSecretsService:
    """Env-seed + DB-overlay platform credential view, TTL-cached."""

    def __init__(
        self,
        *,
        store: PlatformSecretStore,
        settings: Settings,
        ttl_s: float = 30.0,
    ) -> None:
        self._store = store
        self._settings = settings
        self._ttl_s = ttl_s
        self._provider_cache: dict[Provider, str] | None = None
        self._tool_cache: dict[Tool, str] | None = None
        self._tenant_provider_cache: dict[UUID, dict[Provider, str | None]] = {}
        self._tenant_tool_cache: dict[UUID, dict[Tool, str | None]] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def effective_provider_credentials(self) -> dict[Provider, str]:
        """Merged provider → secret_ref view (env seed + enabled DB rows)."""
        await self._maybe_refresh()
        return dict(self._provider_cache or {})

    async def effective_tool_credentials(self) -> dict[Tool, str]:
        """Merged tool → secret_ref view (env seed + enabled DB rows)."""
        await self._maybe_refresh()
        return dict(self._tool_cache or {})

    async def effective_provider_credentials_for(self, tenant_id: UUID) -> dict[Provider, str]:
        """Tenant-effective provider view — Stream HX-8 (Mini-ADR HX-H2).

        Platform merged view, then tenant override rows on top: an enabled
        row overrides the key, a disabled row suppresses it for this tenant
        (mirroring the platform-row P-12 semantics — no fallback); keys
        without a tenant row pass through unchanged.
        """
        await self._maybe_refresh()
        merged: dict[Provider, str] = dict(self._provider_cache or {})
        for provider, ref in self._tenant_provider_cache.get(tenant_id, {}).items():
            if ref is None:
                merged.pop(provider, None)
            else:
                merged[provider] = ref
        return merged

    async def effective_tool_credentials_for(self, tenant_id: UUID) -> dict[Tool, str]:
        """Tenant-effective tool view — Stream HX-8 (see provider twin)."""
        await self._maybe_refresh()
        merged: dict[Tool, str] = dict(self._tool_cache or {})
        for tool, ref in self._tenant_tool_cache.get(tenant_id, {}).items():
            if ref is None:
                merged.pop(tool, None)
            else:
                merged[tool] = ref
        return merged

    def invalidate(self) -> None:
        """Drop the cache so the next read reloads from env + DB."""
        self._expires_at = 0.0

    async def _maybe_refresh(self) -> None:
        if self._provider_cache is not None and time.monotonic() < self._expires_at:
            return
        async with self._lock:
            if self._provider_cache is not None and time.monotonic() < self._expires_at:
                return
            await self._reload()

    async def _reload(self) -> None:
        # Env seed first; DB rows then override per key (enabled → set,
        # disabled → suppress). Platform rows are tenant-less, so the store
        # reads run inside bypass_rls_session().
        providers: dict[Provider, str] = dict(
            self._settings.effective_platform_provider_credentials
        )
        tools: dict[Tool, str] = dict(self._settings.effective_platform_tool_credentials)
        async with bypass_rls_session():
            provider_rows = await self._store.list_providers()
            tool_rows = await self._store.list_tools()
            tenant_provider_rows = await self._store.list_tenant_providers()
            tenant_tool_rows = await self._store.list_tenant_tools()
        for row in provider_rows:
            if row.enabled:
                providers[row.provider] = row.secret_ref
            else:
                providers.pop(row.provider, None)
        for row in tool_rows:
            if row.enabled:
                tools[row.tool] = row.secret_ref
            else:
                tools.pop(row.tool, None)
        # Tenant overlays (Stream HX-8): ref string = enabled override,
        # None = disabled row (suppress for that tenant, Mini-ADR HX-H2).
        tenant_providers: dict[UUID, dict[Provider, str | None]] = {}
        for trow in tenant_provider_rows:
            tenant_providers.setdefault(trow.tenant_id, {})[trow.provider] = (
                trow.secret_ref if trow.enabled else None
            )
        tenant_tools: dict[UUID, dict[Tool, str | None]] = {}
        for trow2 in tenant_tool_rows:
            tenant_tools.setdefault(trow2.tenant_id, {})[trow2.tool] = (
                trow2.secret_ref if trow2.enabled else None
            )
        self._provider_cache = providers
        self._tool_cache = tools
        self._tenant_provider_cache = tenant_providers
        self._tenant_tool_cache = tenant_tools
        _tenant_overrides_gauge.set(len(tenant_provider_rows) + len(tenant_tool_rows))
        self._expires_at = time.monotonic() + self._ttl_s
