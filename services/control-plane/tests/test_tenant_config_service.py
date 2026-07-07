"""Unit tests for :class:`TenantConfigService` — Stream C.7."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from control_plane.audit import build_default_audit_logger
from control_plane.tenancy import TenantConfigNotConfiguredError, TenantConfigService
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.persistence.tenant_config import InMemoryTenantConfigStore
from expert_work.protocol import AuditAction, AuditQuery, TenantConfigPatch, TenantPlan


@pytest.fixture
def store() -> InMemoryTenantConfigStore:
    return InMemoryTenantConfigStore()


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
def service(
    store: InMemoryTenantConfigStore, audit_store: InMemoryAuditLogStore
) -> TenantConfigService:
    return TenantConfigService(
        store=store,
        audit_logger=build_default_audit_logger(audit_store),
        ttl_s=60.0,
    )


@pytest.mark.asyncio
async def test_get_missing_raises_not_configured(service: TenantConfigService) -> None:
    with pytest.raises(TenantConfigNotConfiguredError):
        await service.get(tenant_id=uuid4(), actor_id="x")


@pytest.mark.asyncio
async def test_first_upsert_requires_display_name(service: TenantConfigService) -> None:
    with pytest.raises(ValueError, match="display_name"):
        await service.upsert(
            tenant_id=uuid4(),
            patch=TenantConfigPatch(plan=TenantPlan.PRO),
            actor_id="admin",
        )


@pytest.mark.asyncio
async def test_upsert_then_get_round_trip(
    service: TenantConfigService, audit_store: InMemoryAuditLogStore
) -> None:
    tenant = uuid4()
    created = await service.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(
            display_name="ACME Corp",
            plan=TenantPlan.PRO,
            mcp_allowlist=["github-mcp", "jira-mcp"],
        ),
        actor_id="admin",
    )
    assert created.display_name == "ACME Corp"
    assert created.plan is TenantPlan.PRO
    assert created.mcp_allowlist == ["github-mcp", "jira-mcp"]
    assert created.updated_by == "admin"

    # get from cache (no audit emit because it's a cache HIT).
    fetched = await service.get(tenant_id=tenant, actor_id="reader")
    assert fetched.display_name == "ACME Corp"

    # Write audit is in the log.
    page = await audit_store.query(
        AuditQuery(tenant_id=tenant, action=AuditAction.TENANT_CONFIG_WRITE)
    )
    assert len(page.entries) == 1
    assert "display_name" in page.entries[0].details.get("fields", [])


@pytest.mark.asyncio
async def test_partial_upsert_merges_unset_fields(service: TenantConfigService) -> None:
    tenant = uuid4()
    await service.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(
            display_name="ACME",
            mcp_allowlist=["github-mcp"],
            pii_fields=["email"],
        ),
        actor_id="admin",
    )
    # Only update the plan; mcp_allowlist + pii_fields must survive.
    updated = await service.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(plan=TenantPlan.ENTERPRISE),
        actor_id="admin",
    )
    assert updated.plan is TenantPlan.ENTERPRISE
    assert updated.mcp_allowlist == ["github-mcp"]
    assert updated.pii_fields == ["email"]


@pytest.mark.asyncio
async def test_cache_hit_avoids_store_call(
    service: TenantConfigService, store: InMemoryTenantConfigStore
) -> None:
    tenant = uuid4()
    await service.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="ACME"),
        actor_id="admin",
    )

    # Reach into the store and corrupt it. If the cache works the
    # next ``get`` does not see the corruption.
    store._rows.pop(tenant)  # type: ignore[attr-defined]

    cached = await service.get(tenant_id=tenant, actor_id=None)
    assert cached.display_name == "ACME"


@pytest.mark.asyncio
async def test_cache_expiry_falls_back_to_store(
    audit_store: InMemoryAuditLogStore, store: InMemoryTenantConfigStore
) -> None:
    # Short TTL so we can observe expiry.
    svc = TenantConfigService(
        store=store,
        audit_logger=build_default_audit_logger(audit_store),
        ttl_s=0.05,
    )
    tenant = uuid4()
    await svc.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="initial"),
        actor_id="admin",
    )

    # Mutate the store directly behind the service's back.
    fresh = await store.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="hot-patched"),
        actor_id="admin2",
    )
    assert fresh.display_name == "hot-patched"

    # Cache still serves the stale row.
    cached = await svc.get(tenant_id=tenant, actor_id=None)
    assert cached.display_name == "initial"

    time.sleep(0.06)
    refreshed = await svc.get(tenant_id=tenant, actor_id=None)
    assert refreshed.display_name == "hot-patched"


@pytest.mark.asyncio
async def test_invalidate_drops_cache(
    audit_store: InMemoryAuditLogStore, store: InMemoryTenantConfigStore
) -> None:
    svc = TenantConfigService(
        store=store,
        audit_logger=build_default_audit_logger(audit_store),
        ttl_s=60.0,
    )
    tenant = uuid4()
    await svc.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="cached"),
        actor_id="admin",
    )
    # Mutate behind the back, invalidate, observe refreshed read.
    await store.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="updated"),
        actor_id="admin",
    )
    svc.invalidate(tenant)
    refreshed = await svc.get(tenant_id=tenant, actor_id=None)
    assert refreshed.display_name == "updated"


@pytest.mark.asyncio
async def test_read_audit_only_on_cache_miss(
    service: TenantConfigService, audit_store: InMemoryAuditLogStore
) -> None:
    tenant = uuid4()
    await service.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="ACME"),
        actor_id="admin",
    )
    # First ``get`` after upsert: cache HIT (upsert primes it), no read audit.
    await service.get(tenant_id=tenant, actor_id="reader1")
    await service.get(tenant_id=tenant, actor_id="reader2")
    page = await audit_store.query(
        AuditQuery(tenant_id=tenant, action=AuditAction.TENANT_CONFIG_READ)
    )
    assert page.entries == []

    # Invalidate → next get is a MISS → audit emits.
    service.invalidate(tenant)
    await service.get(tenant_id=tenant, actor_id="reader-after-miss")
    page = await audit_store.query(
        AuditQuery(tenant_id=tenant, action=AuditAction.TENANT_CONFIG_READ)
    )
    assert len(page.entries) == 1
    assert page.entries[0].actor_id == "reader-after-miss"
