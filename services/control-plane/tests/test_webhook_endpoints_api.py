"""End-to-end tests for the HX-9 webhook-endpoint CRUD API (STREAM-HX § 13)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_DEFAULT_TENANT = DEFAULT_DEV_TENANT_ID


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
async def client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        max_webhook_endpoints_per_tenant=2,  # low cap so the quota test is cheap
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as c:
        yield c


async def _create(
    client: AsyncClient,
    *,
    name: str = "ops",
    url: str = "https://hooks.example.com/ingest",
    event_types: list[str] | None = None,
) -> dict[str, object]:
    resp = await client.post(
        "/v1/webhook-endpoints",
        json={
            "name": name,
            "url": url,
            "event_types": event_types or ["run.completed"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_create_shows_secret_once_then_never(client: AsyncClient) -> None:
    created = await _create(client)
    assert created["secret"]  # plaintext shown at creation
    assert created["enabled"] is True
    assert created["event_types"] == ["run.completed"]

    got = await client.get(f"/v1/webhook-endpoints/{created['id']}")
    assert got.status_code == 200
    assert "secret" not in got.json()  # never again


@pytest.mark.asyncio
async def test_create_rejects_ssrf_url(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/webhook-endpoints",
        json={
            "name": "evil",
            "url": "http://169.254.169.254/latest",
            "event_types": ["run.failed"],
        },
    )
    assert resp.status_code == 422
    assert "url" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_rejects_unknown_event_type(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/webhook-endpoints",
        json={"name": "x", "url": "https://h.example.com", "event_types": ["run.exploded"]},
    )
    assert resp.status_code == 422
    assert "unknown event_types" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_name_conflicts(client: AsyncClient) -> None:
    await _create(client, name="dup")
    resp = await client.post(
        "/v1/webhook-endpoints",
        json={"name": "dup", "url": "https://h.example.com", "event_types": ["run.completed"]},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_quota_exhausted(client: AsyncClient) -> None:
    await _create(client, name="a")
    await _create(client, name="b")
    resp = await client.post(
        "/v1/webhook-endpoints",
        json={"name": "c", "url": "https://h.example.com", "event_types": ["run.completed"]},
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_list_and_get_404(client: AsyncClient) -> None:
    await _create(client, name="one")
    await _create(client, name="two")
    listed = await client.get("/v1/webhook-endpoints")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert body["cross_tenant"] is False

    missing = await client.get(f"/v1/webhook-endpoints/{uuid4()}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_patch_updates_fields(client: AsyncClient) -> None:
    created = await _create(client, name="patch-me")
    resp = await client.patch(
        f"/v1/webhook-endpoints/{created['id']}",
        json={"enabled": False, "event_types": ["run.completed", "approval.requested"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert set(body["event_types"]) == {"run.completed", "approval.requested"}


@pytest.mark.asyncio
async def test_patch_rejects_ssrf_url(client: AsyncClient) -> None:
    created = await _create(client, name="patch-ssrf")
    resp = await client.patch(
        f"/v1/webhook-endpoints/{created['id']}",
        json={"url": "http://127.0.0.1:8080/x"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete(client: AsyncClient) -> None:
    created = await _create(client, name="bye")
    resp = await client.delete(f"/v1/webhook-endpoints/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    again = await client.delete(f"/v1/webhook-endpoints/{created['id']}")
    assert again.status_code == 404
