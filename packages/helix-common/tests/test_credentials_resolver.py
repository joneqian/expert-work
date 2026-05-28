"""Stream O Mini-ADR O-3 — CredentialsResolver contract.

Covers the 4 mode x role paths + 4 failure paths:

* platform / provider OK
* platform / tool OK
* tenant / provider OK
* tenant / tool OK
* platform / provider missing → CredentialsResolverError(mode=platform)
* platform / tool missing → CredentialsResolverError(mode=platform)
* tenant / provider missing → CredentialsResolverError(mode=tenant)
* tenant / tool missing → CredentialsResolverError(mode=tenant)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from helix_agent.common.credentials import (
    CredentialsResolver,
    CredentialsResolverError,
)
from helix_agent.protocol import TenantConfigRecord, TenantPlan

_NOW = datetime.now(UTC)
_TENANT = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


def _record(
    *,
    mode: str,
    model_creds: dict[str, str] | None = None,
    tool_creds: dict[str, str] | None = None,
) -> TenantConfigRecord:
    return TenantConfigRecord(
        tenant_id=_TENANT,
        display_name="Test",
        plan=TenantPlan.FREE,
        credentials_mode=mode,  # type: ignore[arg-type]
        model_credentials_ref=model_creds or {},
        tool_credentials=tool_creds or {},  # type: ignore[arg-type]
        created_at=_NOW,
        updated_at=_NOW,
        updated_by="tester",
    )


class _FakeTenantConfigGetter:
    def __init__(self, record: TenantConfigRecord) -> None:
        self._record = record

    async def get(self, *, tenant_id: UUID) -> TenantConfigRecord:
        return self._record


def _resolver(
    *,
    tenant_record: TenantConfigRecord,
    platform_provs: dict[str, str] | None = None,
    platform_tools: dict[str, str] | None = None,
) -> CredentialsResolver:
    return CredentialsResolver(
        platform_provider_credentials=platform_provs or {},  # type: ignore[arg-type]
        platform_tool_credentials=platform_tools or {},  # type: ignore[arg-type]
        tenant_config_getter=_FakeTenantConfigGetter(tenant_record),
    )


# ─── platform mode / OK paths ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_platform_mode_provider_resolves_to_platform_secret() -> None:
    resolver = _resolver(
        tenant_record=_record(mode="platform"),
        platform_provs={"anthropic": "kms://platform/anthropic"},
    )
    secret = await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert secret == "kms://platform/anthropic"


@pytest.mark.asyncio
async def test_platform_mode_tool_resolves_to_platform_secret() -> None:
    resolver = _resolver(
        tenant_record=_record(mode="platform"),
        platform_tools={"web_search": "kms://platform/tavily"},
    )
    secret = await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert secret == "kms://platform/tavily"


# ─── platform mode / missing → raise (operator misconfig) ─────────────


@pytest.mark.asyncio
async def test_platform_mode_provider_missing_raises() -> None:
    resolver = _resolver(
        tenant_record=_record(mode="platform"),
        platform_provs={},
    )
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert exc_info.value.mode == "platform"
    assert exc_info.value.kind == "provider"
    assert exc_info.value.key == "anthropic"


@pytest.mark.asyncio
async def test_platform_mode_tool_missing_raises() -> None:
    resolver = _resolver(
        tenant_record=_record(mode="platform"),
        platform_tools={},
    )
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert exc_info.value.mode == "platform"
    assert exc_info.value.kind == "tool"


# ─── tenant mode / OK paths ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_mode_provider_resolves_to_tenant_secret() -> None:
    resolver = _resolver(
        tenant_record=_record(
            mode="tenant",
            model_creds={"openai": "kms://acme/openai"},
        ),
        # Platform creds exist for openai but are NOT used in tenant mode.
        platform_provs={"openai": "kms://platform/openai"},
    )
    secret = await resolver.resolve_provider(tenant_id=_TENANT, provider="openai")
    assert secret == "kms://acme/openai"


@pytest.mark.asyncio
async def test_tenant_mode_tool_resolves_to_tenant_secret() -> None:
    resolver = _resolver(
        tenant_record=_record(
            mode="tenant",
            tool_creds={"web_search": "kms://acme/tavily"},
        ),
        platform_tools={"web_search": "kms://platform/tavily"},
    )
    secret = await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert secret == "kms://acme/tavily"


# ─── tenant mode / missing → fail-fast (no silent platform fallback) ──


@pytest.mark.asyncio
async def test_tenant_mode_provider_missing_raises_no_platform_fallback() -> None:
    resolver = _resolver(
        tenant_record=_record(mode="tenant", model_creds={}),
        # Platform creds exist for anthropic but resolver does NOT
        # fall back — tenant mode means tenant-only per Mini-ADR O-3.
        platform_provs={"anthropic": "kms://platform/anthropic"},
    )
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert exc_info.value.mode == "tenant"
    assert exc_info.value.kind == "provider"


@pytest.mark.asyncio
async def test_tenant_mode_tool_missing_raises_no_platform_fallback() -> None:
    resolver = _resolver(
        tenant_record=_record(mode="tenant", tool_creds={}),
        platform_tools={"web_search": "kms://platform/tavily"},
    )
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert exc_info.value.mode == "tenant"
    assert exc_info.value.kind == "tool"
