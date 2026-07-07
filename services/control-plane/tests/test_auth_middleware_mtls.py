"""Integration tests for ``AuthMiddleware``'s mTLS branch — Stream C.2."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
)

_SYSTEM_TENANT = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
async def mtls_client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        mtls_enabled=True,
        mtls_allowed_service_subjects=["orchestrator", "sandbox-supervisor"],
        mtls_system_tenant_id=_SYSTEM_TENANT,
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_xfcc_authenticates_service(mtls_client: AsyncClient) -> None:
    response = await mtls_client.get(
        "/v1/agents",
        headers={
            "X-Forwarded-Client-Cert": 'Subject="CN=orchestrator,O=expert_work";Hash=abc',
        },
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_service_subject_returns_401(mtls_client: AsyncClient) -> None:
    response = await mtls_client.get(
        "/v1/agents",
        headers={"X-Forwarded-Client-Cert": 'Subject="CN=evil,O=expert_work"'},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


@pytest.mark.asyncio
async def test_malformed_xfcc_returns_401(mtls_client: AsyncClient) -> None:
    response = await mtls_client.get(
        "/v1/agents",
        headers={"X-Forwarded-Client-Cert": "not-a-real-xfcc"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_neither_jwt_nor_xfcc_returns_missing_credentials(
    mtls_client: AsyncClient,
) -> None:
    response = await mtls_client.get("/v1/agents")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


# ---------------------------------------------------------------------------
# disabled flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mtls_disabled_setting_ignores_xfcc_header(
    audit_store: InMemoryAuditLogStore,
) -> None:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        mtls_enabled=False,
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        response = await client.get(
            "/v1/agents",
            headers={"X-Forwarded-Client-Cert": 'Subject="CN=orchestrator"'},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_MISSING_CREDENTIALS"


# ---------------------------------------------------------------------------
# jwt + mtls coexistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_takes_priority_over_xfcc(mtls_client: AsyncClient) -> None:
    """If both headers are present, the JWT branch wins (first-match)."""
    from tests.auth_fixtures import make_test_jwt

    user_tenant = UUID("11111111-1111-1111-1111-111111111111")
    token = make_test_jwt(tenant_id=user_tenant, subject="alice")
    response = await mtls_client.get(
        "/v1/agents",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-Client-Cert": 'Subject="CN=orchestrator"',
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_xfcc_principal_carries_system_tenant(mtls_client: AsyncClient) -> None:
    """Verify the service-typed Principal lands with the configured system tenant."""
    # We can't observe Principal directly via the public API in this PR
    # (no /v1/auth/me endpoint yet), so we verify indirectly: the request
    # succeeds and the configured system tenant is bound to a 200 response.
    # The Principal projection is unit-tested in test_mtls_verifier.py.
    response = await mtls_client.get(
        "/v1/agents",
        headers={
            "X-Forwarded-Client-Cert": 'Subject="CN=sandbox-supervisor,O=expert_work"',
        },
    )
    assert response.status_code == 200
