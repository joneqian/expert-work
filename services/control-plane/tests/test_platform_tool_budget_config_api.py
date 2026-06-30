"""Endpoint tests for ``/v1/platform/tool-budget-config`` — Phase 3.

Mirrors ``test_platform_judge_config_api.py``: the full ``create_app`` harness
wires ``platform_tool_budget_config_service`` onto ``app.state``; a system_admin
role_binding is seeded; principal via JWT.
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
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    # Pin the env default ON so an unconfigured platform reads effective=True.
    monkeypatch.delenv("HELIX_TOOL_OUTPUT_BUDGET", raising=False)
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=jwt_verifier)
    sys_admin_id = await _seed_admin(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client, sys_admin_id


@pytest.mark.asyncio
async def test_non_admin_forbidden(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, _ = admin_client
    resp = await client.get("/v1/platform/tool-budget-config", headers=_headers(uuid4()))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "PLATFORM_SCOPE_FORBIDDEN"


@pytest.mark.asyncio
async def test_get_unset_uses_env_default(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    resp = await client.get("/v1/platform/tool-budget-config", headers=_headers(admin))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["enabled"] is None  # not configured → env default
    assert data["effective"] is True


@pytest.mark.asyncio
async def test_put_off_then_get_reflects(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    resp = await client.put(
        "/v1/platform/tool-budget-config", headers=_headers(admin), json={"enabled": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"enabled": False, "effective": False}
    get_resp = await client.get("/v1/platform/tool-budget-config", headers=_headers(admin))
    assert get_resp.json()["data"] == {"enabled": False, "effective": False}


@pytest.mark.asyncio
async def test_put_on_after_off(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    await client.put(
        "/v1/platform/tool-budget-config", headers=_headers(admin), json={"enabled": False}
    )
    resp = await client.put(
        "/v1/platform/tool-budget-config", headers=_headers(admin), json={"enabled": True}
    )
    assert resp.json()["data"] == {"enabled": True, "effective": True}


@pytest.mark.asyncio
async def test_put_non_admin_forbidden(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, _ = admin_client
    resp = await client.put(
        "/v1/platform/tool-budget-config", headers=_headers(uuid4()), json={"enabled": False}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_rejects_unknown_field(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    resp = await client.put(
        "/v1/platform/tool-budget-config",
        headers=_headers(admin),
        json={"enabled": True, "bogus": 1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_emits_audit(admin_client: tuple[AsyncClient, UUID]) -> None:
    client, admin = admin_client
    resp = await client.put(
        "/v1/platform/tool-budget-config", headers=_headers(admin), json={"enabled": False}
    )
    assert resp.status_code == 200, resp.text
    store = client._transport.app.state.audit_logger._store  # type: ignore[attr-defined]
    entries = list(store._rows.values())
    matched = [e for e in entries if e.action.value == "platform_tool_budget_config:updated"]
    assert matched, "expected a PLATFORM_TOOL_BUDGET_UPDATED audit row"
