"""Stream O — Mini-ADR O-3. ``CredentialsResolver``.

Single source of truth for "which secret_ref do I use for this
(tenant, provider/tool) pair?".

Backed by:
* Platform Catalog — supplied at construction time (read once at
  control-plane startup from settings; see Mini-ADR O-1).
* Tenant config — looked up per call via a ``TenantConfigGetter``
  callable; the control-plane wires in :class:`TenantConfigService`
  whose existing TTL cache (30s) bounds the read cost.

Returns a ``secret://`` or ``kms://`` URI; the
:class:`~helix_agent.runtime.secret_store.SecretStore` (Stream F.6)
resolves the URI to the actual API key value at LLM-call time.

Per Mini-ADR O-3 there is **no silent fallback** between modes — a
tenant in ``credentials_mode='tenant'`` with a missing credential
raises :class:`CredentialsResolverError` and the caller surfaces a
401 / `CREDENTIALS_RESOLVE_FAILED` audit, instead of using the
platform key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from helix_agent.protocol import (
    CredentialsMode,
    Provider,
    TenantConfigRecord,
    Tool,
)


class CredentialsResolverError(LookupError):
    """Raised when a credentials lookup fails.

    Two distinct shapes:
    * platform mode + platform_*_credentials missing the requested entry
      — operator misconfiguration; runbook walks fix.
    * tenant mode + tenant's own credentials missing the entry — user
      misconfiguration (failed to add a credential before switching
      mode, or added a new agent referencing a provider they didn't
      pre-stage). 401 fail-fast at the caller.
    """

    def __init__(
        self,
        message: str,
        *,
        mode: CredentialsMode,
        kind: str,
        key: str,
    ) -> None:
        super().__init__(message)
        self.mode = mode
        self.kind = kind  # "provider" | "tool"
        self.key = key  # provider name or tool name


class TenantConfigGetter(Protocol):
    """Minimal interface the resolver needs from the tenant config layer.

    The control-plane's :class:`TenantConfigService` satisfies this
    naturally; tests pass a Fake that returns scripted records."""

    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord:
        """Return the tenant's config, raising on unknown id."""


class CredentialsResolver:
    """Resolves ``(tenant, provider/tool) → secret_ref``.

    The resolver is stateless aside from the platform catalogs supplied
    at construction; the tenant lookup is delegated. Concurrency-safe
    by virtue of being immutable.
    """

    def __init__(
        self,
        *,
        platform_provider_credentials: dict[Provider, str],
        platform_tool_credentials: dict[Tool, str],
        tenant_config_getter: TenantConfigGetter,
        platform_provider_getter: Callable[[], Awaitable[dict[Provider, str]]] | None = None,
        platform_tool_getter: Callable[[], Awaitable[dict[Tool, str]]] | None = None,
    ) -> None:
        self._platform_providers = dict(platform_provider_credentials)
        self._platform_tools = dict(platform_tool_credentials)
        self._tenant_config = tenant_config_getter
        # Stream P (Mini-ADR P-9) — optional live sources (env + DB overlay).
        # When provided they win over the frozen dicts above, so a platform
        # admin's runtime credential change takes effect without a restart.
        # Back-compat: callers passing only the static dicts are unchanged.
        self._platform_provider_getter = platform_provider_getter
        self._platform_tool_getter = platform_tool_getter

    async def resolve_provider(
        self,
        *,
        tenant_id: UUID,
        provider: Provider,
    ) -> str:
        """Return the platform secret_ref for this provider.

        Stream Y-1: LLM credentials are platform-exclusive — tenant BYOK
        (``credentials_mode='tenant'``) has been removed. Raises
        :class:`CredentialsResolverError` when the platform catalog is
        missing the provider. ``tenant_id`` is still validated for existence.
        """
        # Validate the tenant exists (raises on unknown id); platform
        # secret_refs are tenant-independent so cfg is not otherwise read.
        await self._tenant_config.get(tenant_id=tenant_id)
        secret_ref = (await self._effective_providers()).get(provider)
        if secret_ref is None:
            msg = (
                f"platform credentials missing for provider={provider}. "
                "Add to settings.platform_provider_credentials."
            )
            raise CredentialsResolverError(msg, mode="platform", kind="provider", key=provider)
        return secret_ref

    async def resolve_tool(
        self,
        *,
        tenant_id: UUID,
        tool: Tool,
    ) -> str:
        """Return the platform secret_ref for this tool.

        Same failure semantics as :meth:`resolve_provider`; Stream Y-1
        platform-exclusive (tenant BYOK removed)."""
        await self._tenant_config.get(tenant_id=tenant_id)
        secret_ref = (await self._effective_tools()).get(tool)
        if secret_ref is None:
            msg = (
                f"platform credentials missing for tool={tool}. "
                "Add to settings.platform_tool_credentials."
            )
            raise CredentialsResolverError(msg, mode="platform", kind="tool", key=tool)
        return secret_ref

    async def _effective_providers(self) -> dict[Provider, str]:
        if self._platform_provider_getter is not None:
            return await self._platform_provider_getter()
        return self._platform_providers

    async def _effective_tools(self) -> dict[Tool, str]:
        if self._platform_tool_getter is not None:
            return await self._platform_tool_getter()
        return self._platform_tools
