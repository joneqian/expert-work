"""Endpoint tests for ``/v1/members`` — Stream R W2 (invite/list/resend/revoke).

A tenant admin (JWT carries ``admin`` role → ``user:write``) onboards members.
Uses a Fake Keycloak so the full flow runs without a live IdP; covers the
batch happy path, per-item conflict isolation, resend compensation, and the
revoke/suspend branches.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.auth import JWTVerifier
from control_plane.keycloak import FakeKeycloakAdminClient
from control_plane.settings import Settings
from expert_work.common.lifecycle import Lifecycle
from tests.auth_fixtures import make_test_jwt


@pytest.fixture
async def admin_app(
    settings: Settings, lifecycle: Lifecycle, jwt_verifier: JWTVerifier
) -> AsyncIterator[tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient]]:
    kc = FakeKeycloakAdminClient()
    app = create_app(
        settings=settings,
        lifecycle=lifecycle,
        jwt_verifier=jwt_verifier,
        keycloak_admin_client=kc,
    )
    tenant_id = uuid4()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client, tenant_id, app, kc


def _admin_headers(tenant_id: UUID) -> dict[str, str]:
    # Default roles=("admin",) → user:write/read.
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=tenant_id, subject=str(uuid4()))}"}


def _viewer_headers(tenant_id: UUID) -> dict[str, str]:
    token = make_test_jwt(tenant_id=tenant_id, subject=str(uuid4()), roles=("viewer",))
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_invite_batch_happy_path(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, kc = admin_app
    resp = await client.post(
        "/v1/members/invite",
        json={
            "invitations": [
                {"email": "a@co.com", "role": "viewer"},
                {"email": "B@Co.com", "role": "operator", "display_name": "Bob"},
            ]
        },
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 201, resp.text
    results = resp.json()["data"]["results"]
    assert len(results) == 2
    assert all(r["error_code"] is None and r["status"] == "invited" for r in results)
    assert results[1]["email"] == "b@co.com"  # normalised
    assert len(kc.users) == 2


@pytest.mark.asyncio
async def test_invite_conflict_is_per_item(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, kc = admin_app
    kc.raise_exists_for.add("taken@co.com")
    resp = await client.post(
        "/v1/members/invite",
        json={
            "invitations": [
                {"email": "taken@co.com", "role": "viewer"},
                {"email": "ok@co.com", "role": "viewer"},
            ]
        },
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 201
    results = {r["email"]: r for r in resp.json()["data"]["results"]}
    assert results["taken@co.com"]["error_code"] == "MEMBER_KEYCLOAK_CONFLICT"
    assert results["ok@co.com"]["error_code"] is None  # the other one still succeeded


@pytest.mark.asyncio
async def test_list_filters_by_status(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, _kc = admin_app
    await client.post(
        "/v1/members/invite",
        json={"invitations": [{"email": "a@co.com", "role": "viewer"}]},
        headers=_admin_headers(tenant_id),
    )
    resp = await client.get("/v1/members", headers=_admin_headers(tenant_id))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["email"] == "a@co.com"

    invited = await client.get("/v1/members?status=invited", headers=_admin_headers(tenant_id))
    assert invited.json()["data"]["total"] == 1
    active = await client.get("/v1/members?status=active", headers=_admin_headers(tenant_id))
    assert active.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_viewer_cannot_invite(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, _kc = admin_app
    resp = await client.post(
        "/v1/members/invite",
        json={"invitations": [{"email": "a@co.com", "role": "viewer"}]},
        headers=_viewer_headers(tenant_id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_revoke_invited_member(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, app, kc = admin_app
    inv = await client.post(
        "/v1/members/invite",
        json={"invitations": [{"email": "a@co.com", "role": "viewer"}]},
        headers=_admin_headers(tenant_id),
    )
    member_id = inv.json()["data"]["results"][0]["member_id"]
    resp = await client.delete(f"/v1/members/{member_id}", headers=_admin_headers(tenant_id))
    assert resp.status_code == 204
    member = await app.state.tenant_member_repo.get(tenant_id=tenant_id, member_id=UUID(member_id))
    assert member is not None and member.status == "revoked"
    assert len(kc.users) == 0  # Keycloak account deleted


@pytest.mark.asyncio
async def test_revoke_missing_member_404(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, _kc = admin_app
    resp = await client.delete(f"/v1/members/{uuid4()}", headers=_admin_headers(tenant_id))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resend_non_invited_409(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, _kc = admin_app
    inv = await client.post(
        "/v1/members/invite",
        json={"invitations": [{"email": "a@co.com", "role": "viewer"}]},
        headers=_admin_headers(tenant_id),
    )
    member_id = inv.json()["data"]["results"][0]["member_id"]
    # Revoke first, then a resend must 409 (not invited any more).
    await client.delete(f"/v1/members/{member_id}", headers=_admin_headers(tenant_id))
    resp = await client.post(f"/v1/members/{member_id}/resend", headers=_admin_headers(tenant_id))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MEMBER_NOT_RESENDABLE"


# --- reset-password (Stream U PR F) -------------------------------------------


@pytest.mark.asyncio
async def test_reset_password_happy_path(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, app, kc = admin_app
    member = await app.state.tenant_member_repo.create(
        tenant_id=tenant_id,
        email="a@co.com",
        role="viewer",
        invited_by=str(uuid4()),
        keycloak_user_id="kc-user-1",
    )
    resp = await client.post(
        f"/v1/members/{member.id}/reset-password",
        json={"password": "hunter2pass"},
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["member_id"] == str(member.id)
    assert kc.password_resets == [("kc-user-1", "hunter2pass", True)]


@pytest.mark.asyncio
async def test_reset_password_no_keycloak_user_409(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, app, kc = admin_app
    member = await app.state.tenant_member_repo.create(
        tenant_id=tenant_id,
        email="a@co.com",
        role="viewer",
        invited_by=str(uuid4()),
        keycloak_user_id=None,
    )
    resp = await client.post(
        f"/v1/members/{member.id}/reset-password",
        json={"password": "hunter2pass"},
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MEMBER_NO_KEYCLOAK_USER"
    assert kc.password_resets == []


@pytest.mark.asyncio
async def test_reset_password_unknown_member_404(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, _app, _kc = admin_app
    resp = await client.post(
        f"/v1/members/{uuid4()}/reset-password",
        json={"password": "hunter2pass"},
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "MEMBER_NOT_FOUND"


@pytest.mark.asyncio
async def test_reset_password_viewer_forbidden(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, app, _kc = admin_app
    member = await app.state.tenant_member_repo.create(
        tenant_id=tenant_id,
        email="a@co.com",
        role="viewer",
        invited_by=str(uuid4()),
        keycloak_user_id="kc-user-1",
    )
    resp = await client.post(
        f"/v1/members/{member.id}/reset-password",
        json={"password": "hunter2pass"},
        headers=_viewer_headers(tenant_id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reset_password_keycloak_unavailable_502(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, app, kc = admin_app
    kc.reset_password_unavailable = True
    member = await app.state.tenant_member_repo.create(
        tenant_id=tenant_id,
        email="a@co.com",
        role="viewer",
        invited_by=str(uuid4()),
        keycloak_user_id="kc-user-1",
    )
    resp = await client.post(
        f"/v1/members/{member.id}/reset-password",
        json={"password": "hunter2pass"},
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "KEYCLOAK_UNAVAILABLE"


@pytest.mark.asyncio
async def test_reset_password_too_short_422(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    client, tenant_id, app, kc = admin_app
    member = await app.state.tenant_member_repo.create(
        tenant_id=tenant_id,
        email="a@co.com",
        role="viewer",
        invited_by=str(uuid4()),
        keycloak_user_id="kc-user-1",
    )
    resp = await client.post(
        f"/v1/members/{member.id}/reset-password",
        json={"password": "short"},
        headers=_admin_headers(tenant_id),
    )
    assert resp.status_code == 422
    assert kc.password_resets == []


@pytest.mark.asyncio
async def test_cross_tenant_list_requires_system_admin(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    """Stream ACCT — ``?tenant_id=*`` is system_admin-only; tenant admin gets 403."""
    client, tenant_id, _app, _kc = admin_app
    resp = await client.get(
        "/v1/members", params={"tenant_id": "*"}, headers=_admin_headers(tenant_id)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "CROSS_TENANT_FORBIDDEN"


@pytest.mark.asyncio
async def test_cross_tenant_list_aggregates_for_system_admin(
    admin_app: tuple[AsyncClient, UUID, object, FakeKeycloakAdminClient],
) -> None:
    from expert_work.protocol import Role

    client, tenant_a, app, _kc = admin_app
    tenant_b = uuid4()
    # Invite one member in each of two tenants.
    for tenant, email in ((tenant_a, "a@t1.com"), (tenant_b, "b@t2.com")):
        r = await client.post(
            "/v1/members/invite",
            json={"invitations": [{"email": email, "role": "viewer"}]},
            headers=_admin_headers(tenant),
        )
        assert r.status_code == 201, r.text

    # Promote a subject to platform system_admin by seeding a platform binding.
    sysadmin = uuid4()
    await app.state.role_binding_repo.create(  # type: ignore[attr-defined]
        subject_type="user",
        subject_id=sysadmin,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="test",
    )
    token = make_test_jwt(tenant_id=uuid4(), subject=str(sysadmin), roles=("admin",))
    resp = await client.get(
        "/v1/members",
        params={"tenant_id": "*"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    tenants_seen = {item["tenant_id"] for item in items}
    assert {str(tenant_a), str(tenant_b)} <= tenants_seen
