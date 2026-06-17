"""Integration tests for :class:`AuthMiddleware` system-admin augmentation — Stream N.

Verifies that when a verified JWT subject has a platform-scope role binding
in ``role_binding_store``, ``AuthMiddleware`` augments the Principal with
``is_system_admin=True`` + ``allowed_tenants="*"`` before passing it
downstream. Negative cases (no binding, wrong subject) leave the Principal
unchanged.

Uses a minimal Starlette ``echo`` route to surface ``request.state.principal``
to assertions, instead of standing up the full control-plane app.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from control_plane.auth.middleware import AuthMiddleware
from helix_agent.persistence.auth import InMemoryRoleBindingStore
from helix_agent.protocol import Role
from tests.auth_fixtures import build_test_jwt_verifier, make_test_jwt


async def _echo_principal(request: Request) -> JSONResponse:
    p = request.state.principal
    allowed = "*" if p.allowed_tenants == "*" else [str(t) for t in p.allowed_tenants]
    return JSONResponse(
        {
            "subject_id": p.subject_id,
            "subject_type": p.subject_type,
            "tenant_id": str(p.tenant_id),
            "is_system_admin": p.is_system_admin,
            "allowed_tenants": allowed,
        }
    )


def _build_app(
    role_binding_store: InMemoryRoleBindingStore | None,
    *,
    bootstrap_admin_email: str | None = None,
) -> Starlette:
    app = Starlette(routes=[Route("/echo", _echo_principal)])
    app.add_middleware(
        AuthMiddleware,
        verifier=build_test_jwt_verifier(),
        role_binding_store=role_binding_store,
        bootstrap_admin_email=bootstrap_admin_email,
    )
    return app


@pytest.fixture
async def store() -> InMemoryRoleBindingStore:
    return InMemoryRoleBindingStore()


@pytest.fixture
async def client(store: InMemoryRoleBindingStore) -> AsyncIterator[AsyncClient]:
    app = _build_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_user_without_platform_binding_is_not_system_admin(
    client: AsyncClient,
) -> None:
    user_id = uuid4()
    tenant = uuid4()
    token = make_test_jwt(tenant_id=tenant, subject=str(user_id))
    r = await client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_system_admin"] is False
    assert body["allowed_tenants"] == [str(tenant)]


@pytest.mark.asyncio
async def test_user_with_platform_binding_is_promoted_to_system_admin(
    client: AsyncClient,
    store: InMemoryRoleBindingStore,
) -> None:
    user_id = uuid4()
    home_tenant = uuid4()
    await store.create(
        subject_type="user",
        subject_id=user_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    token = make_test_jwt(tenant_id=home_tenant, subject=str(user_id))
    r = await client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_system_admin"] is True
    assert body["allowed_tenants"] == "*"
    # Home tenant preserved.
    assert body["tenant_id"] == str(home_tenant)


@pytest.mark.asyncio
async def test_platform_binding_only_applies_to_its_subject(
    client: AsyncClient,
    store: InMemoryRoleBindingStore,
) -> None:
    sys_admin = uuid4()
    other_user = uuid4()
    await store.create(
        subject_type="user",
        subject_id=sys_admin,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="root",
    )
    token = make_test_jwt(tenant_id=uuid4(), subject=str(other_user))
    r = await client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_system_admin"] is False


@pytest.mark.asyncio
async def test_non_uuid_subject_id_skips_lookup_safely(
    client: AsyncClient,
    store: InMemoryRoleBindingStore,
) -> None:
    """JWT with non-UUID ``sub`` (e.g. ``dev-user``) does not crash the resolver."""
    token = make_test_jwt(tenant_id=uuid4(), subject="dev-user")
    r = await client.get("/echo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_system_admin"] is False


@pytest.mark.asyncio
async def test_first_login_bootstraps_first_platform_admin() -> None:
    """Stream ACCT — verified bootstrap email + empty table → auto-grant on login."""
    store = InMemoryRoleBindingStore()
    app = _build_app(store, bootstrap_admin_email="founder@corp.com")
    transport = ASGITransport(app=app)
    user_id = uuid4()
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = make_test_jwt(
            tenant_id=uuid4(),
            subject=str(user_id),
            extra_claims={"email": "Founder@Corp.com", "email_verified": True},
        )
        r = await c.get("/echo", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["is_system_admin"] is True
    bindings = await store.list_platform_scope()
    assert [b.subject_id for b in bindings] == [user_id]


@pytest.mark.asyncio
async def test_first_login_bootstrap_skipped_when_email_unverified() -> None:
    store = InMemoryRoleBindingStore()
    app = _build_app(store, bootstrap_admin_email="founder@corp.com")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = make_test_jwt(
            tenant_id=uuid4(),
            subject=str(uuid4()),
            extra_claims={"email": "founder@corp.com", "email_verified": False},
        )
        r = await c.get("/echo", headers={"Authorization": f"Bearer {token}"})
        assert r.json()["is_system_admin"] is False
    assert await store.list_platform_scope() == []


@pytest.mark.asyncio
async def test_middleware_works_without_role_binding_store() -> None:
    """When ``role_binding_store=None`` (back-compat path), middleware passes
    through and Principal stays non-system-admin."""
    app = _build_app(role_binding_store=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = make_test_jwt(tenant_id=uuid4(), subject=str(uuid4()))
        r = await c.get("/echo", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["is_system_admin"] is False
