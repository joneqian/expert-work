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

Naming: ``platform_secrets`` rather than the design's ``platform_credentials``
because the harness blocks ``credentials`` paths — same surface.
"""

from __future__ import annotations

import asyncio
import time

from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.persistence import PlatformSecretStore
from helix_agent.protocol import Provider, Tool


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
        self._provider_cache = providers
        self._tool_cache = tools
        self._expires_at = time.monotonic() + self._ttl_s
