"""Tenant-aware credentials resolution — Stream HX-8 (Mini-ADR HX-H3).

:class:`TenantOverlayCredentialsResolver` subclasses the shared
``CredentialsResolver`` and swaps the *platform-global* effective view for
the *tenant-effective* one (``PlatformSecretsService.effective_*_for``):
tenant override rows win, disabled rows suppress the key for that tenant
(Mini-ADR HX-H2), tenants without rows see the unchanged platform view —
so every resolve call keeps its exact platform-mode error contract.

Design note (revises §9.2.3 of STREAM-HX-DESIGN): the design sketched new
optional getter kwargs on ``CredentialsResolver`` itself, but the
``expert-work-common/credentials`` path is harness-blocked for edits. A subclass
in control-plane is semantically equivalent — the merge logic still lives
in the service layer and expert-work-common stays untouched (arguably a closer
fit to HX-H3's "resolver stays a pure resolver").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from expert_work.common.credentials import CredentialsResolver, CredentialsResolverError
from expert_work.protocol import Provider, Tool

TenantProviderViewGetter = Callable[[UUID], Awaitable[dict[Provider, str]]]
TenantToolViewGetter = Callable[[UUID], Awaitable[dict[Tool, str]]]
# Stream Y-MK — tenant-effective per-provider ordered key list.
TenantProviderKeysViewGetter = Callable[[UUID], Awaitable[dict[Provider, list[str]]]]


class TenantOverlayCredentialsResolver(CredentialsResolver):
    """``CredentialsResolver`` that resolves through the tenant-effective view."""

    def __init__(
        self,
        *,
        tenant_provider_view: TenantProviderViewGetter,
        tenant_tool_view: TenantToolViewGetter,
        tenant_provider_keys_view: TenantProviderKeysViewGetter | None = None,
        **resolver_kwargs: Any,
    ) -> None:
        super().__init__(**resolver_kwargs)
        self._tenant_provider_view = tenant_provider_view
        self._tenant_tool_view = tenant_tool_view
        self._tenant_provider_keys_view = tenant_provider_keys_view

    async def resolve_provider(self, *, tenant_id: UUID, provider: Provider) -> str:
        # Tenant-existence validation mirrors the base class (raises on
        # unknown id). The tenant view is the FINAL merged view — platform
        # fallback included — so a miss here is a genuine "not configured
        # for this tenant" (override-suppressed or platform-unset alike).
        await self._tenant_config.get(tenant_id=tenant_id)
        secret_ref = (await self._tenant_provider_view(tenant_id)).get(provider)
        if secret_ref is None:
            msg = (
                f"platform credentials missing for provider={provider} "
                f"(tenant-effective view). Configure the platform credential "
                "or remove the tenant's disabled override."
            )
            raise CredentialsResolverError(msg, mode="platform", kind="provider", key=provider)
        return secret_ref

    async def resolve_provider_keys(self, *, tenant_id: UUID, provider: Provider) -> list[str]:
        """Stream Y-MK — the ordered list of secret_refs to try for ``provider``
        (priority asc). A tenant override is a single key replacing the list; a
        disabled override / unset platform credential yields a miss, raised as a
        :class:`CredentialsResolverError` with the same contract as
        :meth:`resolve_provider`. Falls back to the single resolve (wrapped as a
        1-key list) when no keys view was injected."""
        await self._tenant_config.get(tenant_id=tenant_id)
        if self._tenant_provider_keys_view is None:
            return [await self.resolve_provider(tenant_id=tenant_id, provider=provider)]
        refs = (await self._tenant_provider_keys_view(tenant_id)).get(provider)
        if not refs:
            msg = (
                f"platform credentials missing for provider={provider} "
                f"(tenant-effective key list). Configure a platform credential "
                "or remove the tenant's disabled override."
            )
            raise CredentialsResolverError(msg, mode="platform", kind="provider", key=provider)
        return refs

    async def resolve_tool(self, *, tenant_id: UUID, tool: Tool) -> str:
        await self._tenant_config.get(tenant_id=tenant_id)
        secret_ref = (await self._tenant_tool_view(tenant_id)).get(tool)
        if secret_ref is None:
            msg = (
                f"platform credentials missing for tool={tool} "
                f"(tenant-effective view). Configure the platform credential "
                "or remove the tenant's disabled override."
            )
            raise CredentialsResolverError(msg, mode="platform", kind="tool", key=tool)
        return secret_ref
