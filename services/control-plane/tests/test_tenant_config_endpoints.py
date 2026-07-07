"""HTTP tests for ``/v1/tenants/{tid}/config`` — Stream C.7."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import AuditAction, AuditQuery, TenantConfigPatch, TenantPlan
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

_TENANT = DEFAULT_DEV_TENANT_ID


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
async def tc_client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        tenant_rate_limit_capacity=10_000,
        tenant_rate_limit_refill_per_sec=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as c:
        yield c


def _admin_token(tenant: UUID = _TENANT) -> str:
    return make_test_jwt(tenant_id=tenant, subject="admin-user", roles=("admin",))


def _operator_token(tenant: UUID = _TENANT) -> str:
    return make_test_jwt(tenant_id=tenant, subject="op-user", roles=("operator",))


def _viewer_token(tenant: UUID = _TENANT) -> str:
    return make_test_jwt(tenant_id=tenant, subject="viewer-user", roles=("viewer",))


# ---------------------------------------------------------------------------
# Round-trip happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_can_put_then_get_config(tc_client: AsyncClient) -> None:
    token = _admin_token()
    payload = TenantConfigPatch(
        display_name="ACME Corp",
        plan=TenantPlan.PRO,
        mcp_allowlist=["github-mcp"],
        model_credentials_ref={"anthropic": "kms://dev/llm/anthropic-key"},
        pii_fields=["email"],
    )
    put = await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
        json=payload.model_dump(mode="json"),
    )
    assert put.status_code == 200
    data = put.json()["data"]
    assert data["display_name"] == "ACME Corp"
    assert data["plan"] == "pro"
    assert data["mcp_allowlist"] == ["github-mcp"]
    assert data["model_credentials_ref"] == {"anthropic": "kms://dev/llm/anthropic-key"}

    got = await tc_client.get(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert got.status_code == 200
    assert got.json()["data"]["display_name"] == "ACME Corp"


# ---------------------------------------------------------------------------
# 404 path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_404_when_not_seeded(tc_client: AsyncClient) -> None:
    token = _admin_token()
    resp = await tc_client.get(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "TENANT_CONFIG_NOT_FOUND"


@pytest.mark.asyncio
async def test_put_rejects_tenant_credentials_mode(tc_client: AsyncClient) -> None:
    # Stream Y-1 — LLM credentials are platform-exclusive. ``credentials_mode``
    # is a ``Literal["platform"]``, so a PATCH attempting the removed 'tenant'
    # mode is rejected by Pydantic with 422 before reaching the handler.
    token = _admin_token()
    resp = await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "ACME", "credentials_mode": "tenant"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_accepts_valid_rate_limit_override(tc_client: AsyncClient) -> None:
    # Stream C.6 — a well-formed override is stored.
    token = _admin_token()
    resp = await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "ACME", "rate_limit_override": {"requests_per_minute": 600}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["rate_limit_override"] == {"requests_per_minute": 600}


@pytest.mark.asyncio
async def test_put_rejects_bad_rate_limit_override(tc_client: AsyncClient) -> None:
    # Stream C.6 — a malformed override is rejected at write time (422), never
    # silently ignored by the limiter at runtime.
    token = _admin_token()
    resp = await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "ACME", "rate_limit_override": {"requests_per_minute": 0}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "TENANT_CONFIG_INVALID_RATE_LIMIT_OVERRIDE"


@pytest.mark.asyncio
async def test_first_put_requires_display_name(tc_client: AsyncClient) -> None:
    token = _admin_token()
    resp = await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan": "pro"},
    )
    assert resp.status_code == 422
    assert "display_name" in resp.json()["detail"]["code"].lower()


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_can_read_but_not_write(tc_client: AsyncClient) -> None:
    # Seed first with admin so GET has something to return.
    await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        json={"display_name": "ACME"},
    )

    read = await tc_client.get(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {_operator_token()}"},
    )
    assert read.status_code == 200

    write = await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {_operator_token()}"},
        json={"display_name": "Other Name"},
    )
    assert write.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read(tc_client: AsyncClient) -> None:
    await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        json={"display_name": "ACME"},
    )
    read = await tc_client.get(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {_viewer_token()}"},
    )
    assert read.status_code == 200


@pytest.mark.asyncio
async def test_cross_tenant_edit_rejected(tc_client: AsyncClient) -> None:
    other_tenant = uuid4()
    resp = await tc_client.put(
        f"/v1/tenants/{other_tenant}/config",
        headers={"Authorization": f"Bearer {_admin_token()}"},
        json={"display_name": "ACME"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "TENANT_MISMATCH"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_emits_tenant_config_write_audit(
    tc_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    token = _admin_token()
    await tc_client.put(
        f"/v1/tenants/{_TENANT}/config",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "ACME", "plan": "pro"},
    )
    page = await audit_store.query(
        AuditQuery(tenant_id=_TENANT, action=AuditAction.TENANT_CONFIG_WRITE)
    )
    assert len(page.entries) == 1
    fields = page.entries[0].details.get("fields", [])
    assert "display_name" in fields
    assert "plan" in fields
