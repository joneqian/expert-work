"""Endpoint tests for ``POST /v1/tenants`` — Stream P (Mini-ADR P-1/P-2/P-5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.auth import JWTVerifier
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.protocol import Role
from tests.auth_fixtures import make_test_jwt


@pytest.fixture
async def admin_client(
    settings: Settings,
    lifecycle: Lifecycle,
    jwt_verifier: JWTVerifier,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    """App + client; yields the client and the seeded system-admin subject id."""
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=jwt_verifier)
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


def _admin_headers(sys_admin_id: UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {make_test_jwt(tenant_id=uuid4(), subject=str(sys_admin_id))}"
    }


def _non_admin_headers() -> dict[str, str]:
    # A valid-UUID subject with no platform-scope binding → not a system admin.
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=uuid4(), subject=str(uuid4()))}"}


@pytest.mark.asyncio
async def test_system_admin_creates_tenant_server_generated_id(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, sys_admin_id = admin_client
    resp = await client.post(
        "/v1/tenants",
        json={"display_name": "Acme Inc"},
        headers=_admin_headers(sys_admin_id),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["display_name"] == "Acme Inc"
    assert data["plan"] == "free"
    # Server generated a tenant_id.
    UUID(data["tenant_id"])


@pytest.mark.asyncio
async def test_non_admin_cannot_create_tenant(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, _ = admin_client
    resp = await client.post(
        "/v1/tenants",
        json={"display_name": "Sneaky Co"},
        headers=_non_admin_headers(),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PLATFORM_SCOPE_FORBIDDEN"


@pytest.mark.asyncio
async def test_duplicate_client_supplied_tenant_id_conflicts(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, sys_admin_id = admin_client
    tenant_id = str(uuid4())
    headers = _admin_headers(sys_admin_id)

    first = await client.post(
        "/v1/tenants",
        json={"tenant_id": tenant_id, "display_name": "First", "plan": "pro"},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["data"]["tenant_id"] == tenant_id
    assert first.json()["data"]["plan"] == "pro"

    dup = await client.post(
        "/v1/tenants",
        json={"tenant_id": tenant_id, "display_name": "Second"},
        headers=headers,
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "TENANT_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_list_tenants_system_admin_lists_all(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, sys_admin_id = admin_client
    headers = _admin_headers(sys_admin_id)
    seeded_a = str(uuid4())
    seeded_b = str(uuid4())
    for tid, name in ((seeded_a, "Alpha"), (seeded_b, "Beta")):
        created = await client.post(
            "/v1/tenants",
            json={"tenant_id": tid, "display_name": name},
            headers=headers,
        )
        assert created.status_code == 201, created.text

    resp = await client.get("/v1/tenants", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    ids = {t["tenant_id"] for t in body["data"]}
    assert seeded_a in ids and seeded_b in ids
    assert set(body["data"][0].keys()) == {
        "tenant_id",
        "display_name",
        "plan",
        "status",
        "created_at",
    }
    assert all(t["status"] == "active" for t in body["data"])


@pytest.mark.asyncio
async def test_list_tenants_non_admin_forbidden(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, _ = admin_client
    resp = await client.get("/v1/tenants", headers=_non_admin_headers())
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_tenants_pagination(
    admin_client: tuple[AsyncClient, UUID],
) -> None:
    client, sys_admin_id = admin_client
    headers = _admin_headers(sys_admin_id)
    for name in ("One", "Two"):
        created = await client.post(
            "/v1/tenants",
            json={"tenant_id": str(uuid4()), "display_name": name},
            headers=headers,
        )
        assert created.status_code == 201, created.text

    resp = await client.get("/v1/tenants?limit=1&offset=0", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 1
