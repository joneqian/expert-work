"""Tests for ``GET /v1/me`` — Stream H.1b PR 2a.

The endpoint is a pure projection of ``request.state.principal``, so
the matrix that matters is **how the principal got there**, not
business logic:

* JWT (tenant_admin) — base ``Principal.from_jwt_claims`` path
* JWT (system_admin) — augmented via ``resolve_system_admin``
* Anonymous — 401 (auth required, /v1/me is not exempt)

We don't test the API key path here because the dev auth mode used by
:func:`build_test_jwt_verifier` only exercises the JWT verifier;
``test_api_key_verifier`` and ``test_auth_middleware`` already cover
the API-key Principal construction in isolation. Once that Principal
exists, ``/v1/me`` just reflects it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import Role
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

_HOME_TENANT = uuid4()


@pytest.fixture
async def app_state() -> AsyncIterator[tuple[AsyncClient, UUID]]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
    )
    sys_admin_id = uuid4()
    await app.state.role_binding_repo.create(
        subject_type="user",
        subject_id=sys_admin_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="seed",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client, sys_admin_id


@pytest.mark.asyncio
async def test_me_tenant_admin_jwt_projects_principal(
    app_state: tuple[AsyncClient, UUID],
) -> None:
    client, _ = app_state
    subject = str(uuid4())
    token = make_test_jwt(tenant_id=_HOME_TENANT, subject=subject, roles=("admin",))
    response = await client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    data = body["data"]
    assert data["subject_id"] == subject
    assert data["subject_type"] == "user"
    assert data["tenant_id"] == str(_HOME_TENANT)
    assert data["auth_method"] == "jwt"
    assert data["is_system_admin"] is False
    # A real tenant home is not the synthetic platform tenant.
    assert data["home_is_platform"] is False
    # tenant_admin's allowed_tenants defaults to (home,)
    assert data["allowed_tenants"] == [str(_HOME_TENANT)]
    assert "admin" in data["roles"]


@pytest.mark.asyncio
async def test_me_system_admin_jwt_advertises_cross_tenant(
    app_state: tuple[AsyncClient, UUID],
) -> None:
    client, sys_admin_id = app_state
    # System admin still carries a "home tenant" — Stream N keeps the JWT
    # shape untouched and augments via role_binding_repo lookup.
    token = make_test_jwt(tenant_id=_HOME_TENANT, subject=str(sys_admin_id))
    response = await client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["is_system_admin"] is True
    assert data["allowed_tenants"] == "*"
    assert data["tenant_id"] == str(_HOME_TENANT)
    # This system_admin homes to a random (non-platform) tenant ⇒ False.
    assert data["home_is_platform"] is False


@pytest.mark.asyncio
async def test_me_home_is_platform_when_homed_to_platform_tenant(
    app_state: tuple[AsyncClient, UUID],
) -> None:
    """A /setup-provisioned admin homes to the synthetic platform tenant.

    ``home_is_platform`` flags that so the UI can hide the empty platform
    tenant from the TenantSwitcher (Stream ACCT). The default
    ``Settings.platform_tenant_id`` is the well-known ``1111…`` UUID.
    """
    client, sys_admin_id = app_state
    platform_tenant = UUID("11111111-1111-1111-1111-111111111111")
    token = make_test_jwt(tenant_id=platform_tenant, subject=str(sys_admin_id))
    response = await client.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["tenant_id"] == str(platform_tenant)
    assert data["home_is_platform"] is True


@pytest.mark.asyncio
async def test_me_without_credentials_returns_401(
    app_state: tuple[AsyncClient, UUID],
) -> None:
    client, _ = app_state
    response = await client.get("/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "AUTH_MISSING_CREDENTIALS"
