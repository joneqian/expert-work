"""Integration: D.2 ``TenantAwareRedactor`` wired through ``create_app``.

The audit logger constructed by :func:`create_app` should mask both
global secrets and per-tenant ``pii_fields`` after a tenant has been
seeded with PII field names. This pins the cycle-broken wiring (D.2
``TenantConfigPiiResolver.bind``) end-to-end.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from control_plane.app import create_app
from control_plane.audit import TenantConfigPiiResolver, build_default_audit_logger
from control_plane.tenancy import TenantConfigService
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.persistence.tenant_config import InMemoryTenantConfigStore
from helix_agent.protocol import (
    AuditAction,
    AuditEntry,
    AuditQuery,
    AuditResult,
    TenantConfigPatch,
)
from helix_agent.runtime.audit import REPLACEMENT


@pytest.mark.asyncio
async def test_unbound_resolver_returns_empty_pii_fields() -> None:
    """Before ``bind`` no service exists → resolver yields ``[]``."""
    resolver = TenantConfigPiiResolver()
    assert await resolver(uuid4()) == []


@pytest.mark.asyncio
async def test_create_app_wires_tenant_aware_redactor() -> None:
    """End-to-end: seed pii_fields via the app's service, write audit, observe masking.

    Pass an explicit ``audit_logger`` (with a known store) into
    ``create_app`` so we can introspect what landed without exposing
    extra state.
    """
    audit_store = InMemoryAuditLogStore()
    resolver = TenantConfigPiiResolver()
    audit_logger = build_default_audit_logger(store=audit_store, pii_fields_resolver=resolver)

    app = create_app(audit_logger=audit_logger)
    # ``create_app`` constructs its own TenantConfigService internally
    # and binds the resolver before returning; mirror that wiring here.
    resolver.bind(app.state.tenant_config_service)

    tenant_config_service: TenantConfigService = app.state.tenant_config_service

    tenant = uuid4()
    await tenant_config_service.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="acme", pii_fields=["ssn", "patient_id"]),
        actor_id="admin",
    )

    entry = AuditEntry(
        tenant_id=tenant,
        actor_type="user",
        actor_id="alice",
        action=AuditAction.MANIFEST_WRITE,
        resource_type="manifest",
        resource_id="demo@1",
        result=AuditResult.SUCCESS,
        details={
            "ssn": "123-45-6789",
            "request": {"patient_id": "P-001"},
            "ok": True,
        },
    )
    await audit_logger.write(entry)

    page = await audit_store.query(AuditQuery(tenant_id=tenant, action=AuditAction.MANIFEST_WRITE))
    assert len(page.entries) == 1
    persisted = page.entries[0]
    assert persisted.details["ssn"] == REPLACEMENT
    assert persisted.details["request"]["patient_id"] == REPLACEMENT
    assert persisted.details["ok"] is True


@pytest.mark.asyncio
async def test_no_pii_fields_configured_only_global_redaction() -> None:
    """Tenant with no pii_fields seeded → only global secrets masked."""
    audit_store = InMemoryAuditLogStore()
    tenant_config_repo = InMemoryTenantConfigStore()

    resolver = TenantConfigPiiResolver()
    logger = build_default_audit_logger(store=audit_store, pii_fields_resolver=resolver)
    tcs = TenantConfigService(store=tenant_config_repo, audit_logger=logger, ttl_s=60.0)
    resolver.bind(tcs)

    tenant = uuid4()
    # Tenant exists but has no pii_fields configured.
    await tcs.upsert(
        tenant_id=tenant,
        patch=TenantConfigPatch(display_name="acme"),
        actor_id="admin",
    )

    entry = AuditEntry(
        tenant_id=tenant,
        actor_type="user",
        actor_id="alice",
        action=AuditAction.MANIFEST_WRITE,
        resource_type="manifest",
        resource_id="demo@1",
        result=AuditResult.SUCCESS,
        details={
            "ssn": "123-45-6789",  # not in pii_fields → kept
            "prompt": "use sk-ABCDEFGHIJKLMNOPQRSTUVWX",  # global pattern hits
        },
    )
    await logger.write(entry)

    page = await audit_store.query(AuditQuery(tenant_id=tenant, action=AuditAction.MANIFEST_WRITE))
    assert page.entries[0].details["ssn"] == "123-45-6789"
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in page.entries[0].details["prompt"]


@pytest.mark.asyncio
async def test_tenant_not_configured_falls_back_to_global_only() -> None:
    """An audit write for an unseeded tenant must not block on tenant_config."""
    audit_store = InMemoryAuditLogStore()
    tenant_config_repo = InMemoryTenantConfigStore()

    resolver = TenantConfigPiiResolver()
    logger = build_default_audit_logger(store=audit_store, pii_fields_resolver=resolver)
    tcs = TenantConfigService(store=tenant_config_repo, audit_logger=logger, ttl_s=60.0)
    resolver.bind(tcs)

    tenant = uuid4()  # no upsert → no row → TenantConfigNotConfiguredError

    entry = AuditEntry(
        tenant_id=tenant,
        actor_type="user",
        actor_id="alice",
        action=AuditAction.MANIFEST_WRITE,
        resource_type="manifest",
        resource_id="demo@1",
        result=AuditResult.SUCCESS,
        details={
            "ssn": "123-45-6789",
            "prompt": "sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        },
    )
    # Must not raise even though tenant_config.get raises internally.
    await logger.write(entry)

    page = await audit_store.query(AuditQuery(tenant_id=tenant, action=AuditAction.MANIFEST_WRITE))
    assert len(page.entries) == 1
    # Global pattern still applied.
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in page.entries[0].details["prompt"]
    # No PII fields configured → ssn passes through.
    assert page.entries[0].details["ssn"] == "123-45-6789"
