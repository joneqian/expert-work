"""Endpoint tests for ``/v1/platform/billing-config`` — Stream 12.4.

Mirrors ``test_platform_judge_config_api.py``: the full ``create_app`` harness
wires ``platform_billing_config_store`` onto ``app.state``; a system_admin
role_binding is seeded; principal via JWT. The flag is the rollup-enable toggle
the offline billing-rollup job reads.
"""

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


async def _seed_admin(app: object) -> UUID:
    sys_admin_id = uuid4()
    await app.state.role_binding_repo.create(  # type: ignore[attr-defined]
        subject_type="user",
        subject_id=sys_admin_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="seed",
    )
    return sys_admin_id


def _headers(subject: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=uuid4(), subject=str(subject))}"}


@pytest.fixture
async def admin_client(
    settings: Settings,
    lifecycle: Lifecycle,
    jwt_verifier: JWTVerifier,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=jwt_verifier)
    sys_admin_id = await _seed_admin(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client, sys_admin_id


@pytest.mark.asyncio
async def test_non_admin_forbidden(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, _ = admin_client
    resp = await client.get("/v1/platform/billing-config", headers=_headers(uuid4()))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PLATFORM_SCOPE_FORBIDDEN"


@pytest.mark.asyncio
async def test_get_defaults_enabled_when_unset(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    resp = await client.get("/v1/platform/billing-config", headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["rollup_enabled"] is True


@pytest.mark.asyncio
async def test_put_disable_then_get_reflects(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    put = await client.put(
        "/v1/platform/billing-config",
        headers=_headers(admin),
        json={"rollup_enabled": False},
    )
    assert put.status_code == 200, put.text
    assert put.json()["data"]["rollup_enabled"] is False

    get = await client.get("/v1/platform/billing-config", headers=_headers(admin))
    assert get.json()["data"]["rollup_enabled"] is False

    # Re-enabling round-trips back to True.
    again = await client.put(
        "/v1/platform/billing-config",
        headers=_headers(admin),
        json={"rollup_enabled": True},
    )
    assert again.json()["data"]["rollup_enabled"] is True


@pytest.mark.asyncio
async def test_put_forbidden_for_non_admin(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, _ = admin_client
    resp = await client.put(
        "/v1/platform/billing-config",
        headers=_headers(uuid4()),
        json={"rollup_enabled": False},
    )
    assert resp.status_code == 403
