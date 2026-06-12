"""Abstract :class:`PlatformSecretStore` — Stream P (Mini-ADR P-7).

Runtime CRUD over the platform provider/tool secret-ref overlay. Every row is
tenant-less (platform-global), so **callers MUST be inside**
``bypass_rls_session()`` — there is no per-tenant RLS scope to satisfy, exactly
like the ``role_binding`` platform-scope rows.

Naming: ``platform_secret(s)`` rather than the design's ``platform_credential``
because the harness blocks ``credentials`` paths — same surface.
"""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import (
    PlatformProviderSecretRecord,
    PlatformToolSecretRecord,
    Provider,
    TenantProviderSecretRecord,
    TenantToolSecretRecord,
    Tool,
)


class PlatformSecretStore(abc.ABC):
    """Persistence Protocol for platform provider/tool credential refs."""

    @abc.abstractmethod
    async def list_providers(self) -> list[PlatformProviderSecretRecord]:
        """All platform provider secret rows. Caller must bypass RLS."""

    @abc.abstractmethod
    async def get_provider(self, provider: Provider) -> PlatformProviderSecretRecord | None:
        """One provider row, or None. Caller must bypass RLS."""

    @abc.abstractmethod
    async def upsert_provider(
        self,
        *,
        provider: Provider,
        secret_ref: str,
        enabled: bool,
        actor_id: str,
    ) -> PlatformProviderSecretRecord:
        """Insert-or-update a provider secret ref. Caller must bypass RLS."""

    @abc.abstractmethod
    async def delete_provider(self, provider: Provider) -> bool:
        """Delete a provider row; False if it did not exist. Caller must bypass RLS."""

    @abc.abstractmethod
    async def list_tools(self) -> list[PlatformToolSecretRecord]:
        """All platform tool secret rows. Caller must bypass RLS."""

    @abc.abstractmethod
    async def get_tool(self, tool: Tool) -> PlatformToolSecretRecord | None:
        """One tool row, or None. Caller must bypass RLS."""

    @abc.abstractmethod
    async def upsert_tool(
        self,
        *,
        tool: Tool,
        secret_ref: str,
        enabled: bool,
        actor_id: str,
    ) -> PlatformToolSecretRecord:
        """Insert-or-update a tool secret ref. Caller must bypass RLS."""

    @abc.abstractmethod
    async def delete_tool(self, tool: Tool) -> bool:
        """Delete a tool row; False if it did not exist. Caller must bypass RLS."""

    # --- per-tenant overrides (Stream HX-8) ---------------------------

    @abc.abstractmethod
    async def list_tenant_providers(
        self, tenant_id: UUID | None = None
    ) -> list[TenantProviderSecretRecord]:
        """Tenant provider override rows — all tenants when ``tenant_id`` is
        None (the service cache load), one tenant otherwise. Caller must
        bypass RLS."""

    @abc.abstractmethod
    async def upsert_tenant_provider(
        self,
        *,
        tenant_id: UUID,
        provider: Provider,
        secret_ref: str,
        enabled: bool,
        actor_id: str,
    ) -> TenantProviderSecretRecord:
        """Insert-or-update a tenant provider override. Caller must bypass RLS."""

    @abc.abstractmethod
    async def delete_tenant_provider(self, *, tenant_id: UUID, provider: Provider) -> bool:
        """Delete a tenant provider override; False if absent. Caller must bypass RLS."""

    @abc.abstractmethod
    async def list_tenant_tools(
        self, tenant_id: UUID | None = None
    ) -> list[TenantToolSecretRecord]:
        """Tenant tool override rows — all tenants when ``tenant_id`` is None.
        Caller must bypass RLS."""

    @abc.abstractmethod
    async def upsert_tenant_tool(
        self,
        *,
        tenant_id: UUID,
        tool: Tool,
        secret_ref: str,
        enabled: bool,
        actor_id: str,
    ) -> TenantToolSecretRecord:
        """Insert-or-update a tenant tool override. Caller must bypass RLS."""

    @abc.abstractmethod
    async def delete_tenant_tool(self, *, tenant_id: UUID, tool: Tool) -> bool:
        """Delete a tenant tool override; False if absent. Caller must bypass RLS."""
