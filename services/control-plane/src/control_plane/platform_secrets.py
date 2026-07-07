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
from expert_work.common.observability import expert_work_gauge
from expert_work.persistence import PlatformSecretStore
from expert_work.protocol import PlatformProviderSecretRecord, Provider, Tool

_tenant_overrides_gauge = expert_work_gauge(
    "expert_work_platform_credentials_tenant_overrides",
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
        # Y-MK — per-provider ordered key list (priority-sorted); the single
        # cache above is the first/best of each list (embed/rerank compat).
        self._provider_refs_cache: dict[Provider, list[str]] | None = None
        self._tool_cache: dict[Tool, str] | None = None
        self._tenant_provider_cache: dict[UUID, dict[Provider, str | None]] = {}
        self._tenant_tool_cache: dict[UUID, dict[Tool, str | None]] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def effective_provider_credentials(self) -> dict[Provider, str]:
        """Merged provider → *best* secret_ref view (env seed + enabled DB rows).

        Y-MK: the single best key (first by priority) per provider — preserved
        for non-router callers (embed / rerank / aux) that take one key.
        """
        await self._maybe_refresh()
        return dict(self._provider_cache or {})

    async def effective_provider_secret_refs(self) -> dict[Provider, list[str]]:
        """Y-MK — provider → ordered key list (priority asc, then key_id).

        DB rows are authoritative when present: enabled keys form the list;
        if *all* keys for a provider are disabled the provider is suppressed
        (even over an env seed, mirroring the P-12 single-key semantics).
        Providers with no DB rows fall back to the env seed as a 1-key list.
        """
        await self._maybe_refresh()
        return {p: list(v) for p, v in (self._provider_refs_cache or {}).items()}

    async def effective_provider_secret_refs_for(
        self, tenant_id: UUID
    ) -> dict[Provider, list[str]]:
        """Y-MK tenant-effective key list. A tenant override (HX-8) is a single
        key that *replaces* the platform list; a disabled override suppresses
        the provider for that tenant; no override → the full platform list."""
        await self._maybe_refresh()
        merged: dict[Provider, list[str]] = {
            p: list(v) for p, v in (self._provider_refs_cache or {}).items()
        }
        for provider, ref in self._tenant_provider_cache.get(tenant_id, {}).items():
            if ref is None:
                merged.pop(provider, None)
            else:
                merged[provider] = [ref]
        return merged

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
        # Env seed; DB rows then take over per provider. Platform rows are
        # tenant-less, so the store reads run inside bypass_rls_session().
        env_providers: dict[Provider, str] = dict(
            self._settings.effective_platform_provider_credentials
        )
        tools: dict[Tool, str] = dict(self._settings.effective_platform_tool_credentials)
        async with bypass_rls_session():
            provider_rows = await self._store.list_providers()
            tool_rows = await self._store.list_tools()
            tenant_provider_rows = await self._store.list_tenant_providers()
            tenant_tool_rows = await self._store.list_tenant_tools()
        # Y-MK — a provider with ANY DB rows is DB-authoritative: enabled keys
        # (priority asc, then key_id) form its list; all-disabled → suppressed
        # even over an env seed. Providers with no DB rows use the env seed as a
        # 1-key list. Single cache = first/best key of each list.
        db_by_provider: dict[Provider, list[PlatformProviderSecretRecord]] = {}
        for row in provider_rows:
            db_by_provider.setdefault(row.provider, []).append(row)
        provider_refs: dict[Provider, list[str]] = {}
        for provider, rows in db_by_provider.items():
            enabled = sorted((r for r in rows if r.enabled), key=lambda r: (r.priority, r.key_id))
            if enabled:
                provider_refs[provider] = [r.secret_ref for r in enabled]
        for provider, ref in env_providers.items():
            if provider not in db_by_provider:
                provider_refs[provider] = [ref]
        providers: dict[Provider, str] = {p: refs[0] for p, refs in provider_refs.items()}
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
        self._provider_refs_cache = provider_refs
        self._tool_cache = tools
        self._tenant_provider_cache = tenant_providers
        self._tenant_tool_cache = tenant_tools
        _tenant_overrides_gauge.set(len(tenant_provider_rows) + len(tenant_tool_rows))
        self._expires_at = time.monotonic() + self._ttl_s
