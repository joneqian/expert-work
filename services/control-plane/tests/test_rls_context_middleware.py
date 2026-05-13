"""Unit tests for :class:`control_plane.tenancy.RLSContextMiddleware`.

The middleware reads ``request.state.principal`` (populated by
:class:`AuthMiddleware`) and projects ``principal.tenant_id`` into the
:data:`helix_agent.persistence.rls.current_tenant_id_var` ContextVar
for the lifetime of the request.

These tests exercise the project-and-clean-up contract at the HTTP
boundary; SQLAlchemy emission of ``SET LOCAL`` is covered by the
persistence-layer integration test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.persistence.rls import current_tenant_id_var
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

_TENANT = DEFAULT_DEV_TENANT_ID


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


def _settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


@pytest.fixture
async def app_with_probe(
    audit_store: InMemoryAuditLogStore,
) -> AsyncIterator[tuple[FastAPI, list[UUID | None]]]:
    """Build an app with an inline ``/probe`` route that records the ContextVar."""
    settings = _settings()
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
    )

    # ContextVar samples captured by the route — order is request order.
    seen: list[UUID | None] = []

    @app.get("/probe")
    async def _probe() -> dict[str, str | None]:
        observed = current_tenant_id_var.get()
        seen.append(observed)
        return {"tenant": str(observed) if observed is not None else None}

    yield app, seen


@pytest.fixture
async def client(
    app_with_probe: tuple[FastAPI, list[UUID | None]],
) -> AsyncIterator[tuple[AsyncClient, list[UUID | None]]]:
    app, seen = app_with_probe
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as c:
        yield c, seen


@pytest.mark.asyncio
async def test_authenticated_request_sets_tenant_id_in_context(
    client: tuple[AsyncClient, list[UUID | None]],
) -> None:
    c, seen = client
    token = make_test_jwt(tenant_id=_TENANT, subject="dev-user")
    resp = await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"tenant": str(_TENANT)}
    assert seen == [_TENANT]


@pytest.mark.asyncio
async def test_tenant_id_isolated_across_requests(
    client: tuple[AsyncClient, list[UUID | None]],
) -> None:
    """Two requests with different tenants must not bleed into each other."""
    c, seen = client
    tenant_a, tenant_b = uuid4(), uuid4()

    tok_a = make_test_jwt(tenant_id=tenant_a, subject="user-a")
    tok_b = make_test_jwt(tenant_id=tenant_b, subject="user-b")

    resp_a = await c.get("/probe", headers={"Authorization": f"Bearer {tok_a}"})
    resp_b = await c.get("/probe", headers={"Authorization": f"Bearer {tok_b}"})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert seen == [tenant_a, tenant_b]


@pytest.mark.asyncio
async def test_context_var_cleared_after_request(
    client: tuple[AsyncClient, list[UUID | None]],
) -> None:
    """After the request finishes the ContextVar must be back to its default."""
    c, _seen = client
    token = make_test_jwt(tenant_id=_TENANT)
    await c.get("/probe", headers={"Authorization": f"Bearer {token}"})
    # The fixture client lives in *this* asyncio task; the request
    # finished cleanly so the ``reset`` in the middleware should have
    # restored the default (None) for the surrounding context.
    assert current_tenant_id_var.get() is None


@pytest.mark.asyncio
async def test_exempt_path_leaves_context_unset(
    app_with_probe: tuple[FastAPI, list[UUID | None]],
) -> None:
    """``/healthz`` has no principal → ContextVar must stay ``None`` so
    any accidental DB access from an exempt path fails closed under RLS.
    """
    app, _seen = app_with_probe
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as c:
        resp = await c.get("/healthz/live")
        assert resp.status_code == 200
    assert current_tenant_id_var.get() is None
