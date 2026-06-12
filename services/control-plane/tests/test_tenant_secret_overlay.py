"""Tests for :class:`TenantOverlayCredentialsResolver` — Stream HX-8 (HX-H3)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from control_plane.tenant_secret_overlay import TenantOverlayCredentialsResolver
from helix_agent.common.credentials import CredentialsResolverError
from helix_agent.protocol import Provider, TenantConfigRecord, TenantPlan, Tool

_NOW = datetime.now(UTC)
_TENANT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class _FakeTenantConfigGetter:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord:
        self.calls.append(tenant_id)
        return TenantConfigRecord(
            tenant_id=tenant_id,
            display_name="Test",
            plan=TenantPlan.FREE,
            credentials_mode="platform",
            created_at=_NOW,
            updated_at=_NOW,
            updated_by="tester",
        )


def _resolver(
    *,
    provider_view: dict[Provider, str] | None = None,
    tool_view: dict[Tool, str] | None = None,
    tenant_config: _FakeTenantConfigGetter | None = None,
) -> TenantOverlayCredentialsResolver:
    async def _providers(tenant_id: UUID) -> dict[Provider, str]:
        return dict(provider_view or {})

    async def _tools(tenant_id: UUID) -> dict[Tool, str]:
        return dict(tool_view or {})

    return TenantOverlayCredentialsResolver(
        tenant_provider_view=_providers,
        tenant_tool_view=_tools,
        platform_provider_credentials={},
        platform_tool_credentials={},
        tenant_config_getter=tenant_config or _FakeTenantConfigGetter(),
    )


@pytest.mark.asyncio
async def test_resolves_provider_through_tenant_view() -> None:
    resolver = _resolver(provider_view={"anthropic": "kms://tenant/anthropic"})
    ref = await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert ref == "kms://tenant/anthropic"


@pytest.mark.asyncio
async def test_resolves_tool_through_tenant_view() -> None:
    resolver = _resolver(tool_view={"web_search": "kms://tenant/tavily"})
    ref = await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert ref == "kms://tenant/tavily"


@pytest.mark.asyncio
async def test_missing_provider_keeps_platform_error_contract() -> None:
    resolver = _resolver(provider_view={})
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert exc_info.value.mode == "platform"
    assert exc_info.value.kind == "provider"
    assert exc_info.value.key == "anthropic"


@pytest.mark.asyncio
async def test_missing_tool_keeps_platform_error_contract() -> None:
    resolver = _resolver(tool_view={})
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert exc_info.value.mode == "platform"
    assert exc_info.value.kind == "tool"
    assert exc_info.value.key == "web_search"


@pytest.mark.asyncio
async def test_tenant_existence_validated_before_view_lookup() -> None:
    tenant_config = _FakeTenantConfigGetter()
    resolver = _resolver(
        provider_view={"anthropic": "kms://tenant/anthropic"}, tenant_config=tenant_config
    )
    await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert tenant_config.calls == [_TENANT]
