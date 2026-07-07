"""Stream O Mini-ADR O-3 — CredentialsResolver contract.

Stream Y-1 made LLM credentials platform-exclusive, so only the
``platform`` mode paths remain:

* platform / provider OK
* platform / tool OK
* platform / provider missing → CredentialsResolverError(mode=platform)
* platform / tool missing → CredentialsResolverError(mode=platform)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from expert_work.common.credentials import (
    CredentialsResolver,
    CredentialsResolverError,
)
from expert_work.protocol import TenantConfigRecord, TenantPlan

_NOW = datetime.now(UTC)
_TENANT = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


def _record() -> TenantConfigRecord:
    return TenantConfigRecord(
        tenant_id=_TENANT,
        display_name="Test",
        plan=TenantPlan.FREE,
        credentials_mode="platform",
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
        tenant_record=_record(),
        platform_provs={"anthropic": "kms://platform/anthropic"},
    )
    secret = await resolver.resolve_provider(tenant_id=_TENANT, provider="anthropic")
    assert secret == "kms://platform/anthropic"


@pytest.mark.asyncio
async def test_platform_mode_tool_resolves_to_platform_secret() -> None:
    resolver = _resolver(
        tenant_record=_record(),
        platform_tools={"web_search": "kms://platform/tavily"},
    )
    secret = await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert secret == "kms://platform/tavily"


# ─── platform mode / missing → raise (operator misconfig) ─────────────


@pytest.mark.asyncio
async def test_platform_mode_provider_missing_raises() -> None:
    resolver = _resolver(
        tenant_record=_record(),
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
        tenant_record=_record(),
        platform_tools={},
    )
    with pytest.raises(CredentialsResolverError) as exc_info:
        await resolver.resolve_tool(tenant_id=_TENANT, tool="web_search")
    assert exc_info.value.mode == "platform"
    assert exc_info.value.kind == "tool"
