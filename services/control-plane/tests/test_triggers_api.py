"""End-to-end tests for the J.10 trigger CRUD + webhook ingest API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

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

_REPORTER_YAML = """\
apiVersion: helix.io/v1
kind: Agent
metadata:
  name: reporter
  version: "1.0.0"
  tenant: platform-eng
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-5
  system_prompt:
    template: "you report"
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: ["api.anthropic.com"]
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
"""


@pytest.fixture
async def triggers_client() -> AsyncIterator[AsyncClient]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        max_cron_triggers_per_tenant=2,  # low cap so the quota test is cheap
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,  # this suite drives firing directly
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        await client.post("/v1/agents", json={"manifest_yaml": _REPORTER_YAML})
        yield client


def _bare_client(authed: AsyncClient) -> AsyncClient:
    """A client over the same app with no Authorization header."""
    app = authed._transport.app  # type: ignore[attr-defined,union-attr]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://control-plane.test")


async def _create_cron(client: AsyncClient, *, name: str = "nightly") -> dict[str, object]:
    resp = await client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": name,
            "kind": "cron",
            "config": {"expr": "0 9 * * *"},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


# --- CRUD -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_cron_trigger(triggers_client: AsyncClient) -> None:
    body = await _create_cron(triggers_client)
    assert body["kind"] == "cron"
    assert body["enabled"] is True
    assert body["source"] == "api"
    assert "webhook_secret" not in body  # cron triggers have no secret


@pytest.mark.asyncio
async def test_create_cron_rejects_bad_expr(triggers_client: AsyncClient) -> None:
    resp = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "bad",
            "kind": "cron",
            "config": {"expr": "not-a-cron"},
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_webhook_trigger_returns_secret(triggers_client: AsyncClient) -> None:
    resp = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "on-push",
            "kind": "webhook",
            "config": {},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "webhook"
    assert isinstance(body["webhook_secret"], str)
    assert len(body["webhook_secret"]) > 20  # shown once at creation


@pytest.mark.asyncio
async def test_create_duplicate_name_returns_409(triggers_client: AsyncClient) -> None:
    await _create_cron(triggers_client, name="dup")
    resp = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "dup",
            "kind": "cron",
            "config": {"expr": "0 9 * * *"},
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_get_patch_delete(triggers_client: AsyncClient) -> None:
    created = await _create_cron(triggers_client, name="lifecycle")
    trigger_id = created["id"]

    listed = await triggers_client.get("/v1/triggers", params={"agent_name": "reporter"})
    assert listed.status_code == 200
    assert any(t["id"] == trigger_id for t in listed.json()["items"])

    got = await triggers_client.get(f"/v1/triggers/{trigger_id}")
    assert got.status_code == 200

    patched = await triggers_client.patch(f"/v1/triggers/{trigger_id}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = await triggers_client.delete(f"/v1/triggers/{trigger_id}")
    assert deleted.status_code == 200
    assert (await triggers_client.get(f"/v1/triggers/{trigger_id}")).status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_trigger_404(triggers_client: AsyncClient) -> None:
    resp = await triggers_client.get(f"/v1/triggers/{uuid4()}")
    assert resp.status_code == 404


# --- webhook ingest -------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_fires_run_without_jwt(triggers_client: AsyncClient) -> None:
    """A bare (no-JWT) webhook call with the right secret fires a run —
    proving both the AuthMiddleware exemption and the firing path."""
    created = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "hook-fire",
            "kind": "webhook",
            "config": {"seed_input": "go"},
        },
    )
    trigger_id = created.json()["id"]
    secret = created.json()["webhook_secret"]

    async with _bare_client(triggers_client) as bare:
        resp = await bare.post(
            f"/v1/webhooks/{trigger_id}",
            headers={"X-Helix-Webhook-Secret": secret},
        )
    assert resp.status_code == 202

    # Drain the spawned run worker so the loop has no dangling task.
    app = triggers_client._transport.app  # type: ignore[attr-defined,union-attr]
    runs = await app.state.trigger_run_store.list_by_trigger(
        trigger_id=UUID(trigger_id), tenant_id=_DEFAULT_TENANT
    )
    assert len(runs) == 1
    record = app.state.agent_runtime.run_manager.get(runs[0].run_id)
    assert record is not None and record.task is not None
    await record.task


@pytest.mark.asyncio
async def test_webhook_rejects_bad_secret(triggers_client: AsyncClient) -> None:
    created = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "hook-bad",
            "kind": "webhook",
            "config": {},
        },
    )
    trigger_id = created.json()["id"]
    resp = await triggers_client.post(
        f"/v1/webhooks/{trigger_id}",
        headers={"X-Helix-Webhook-Secret": "wrong-secret"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_rejects_missing_secret(triggers_client: AsyncClient) -> None:
    created = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "hook-nosecret",
            "kind": "webhook",
            "config": {},
        },
    )
    trigger_id = created.json()["id"]
    resp = await triggers_client.post(f"/v1/webhooks/{trigger_id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_unknown_trigger_404(triggers_client: AsyncClient) -> None:
    resp = await triggers_client.post(
        f"/v1/webhooks/{uuid4()}",
        headers={"X-Helix-Webhook-Secret": "anything"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cron_trigger_quota_returns_429(triggers_client: AsyncClient) -> None:
    """Creating cron triggers past the per-tenant cap (test cap = 2) is rejected."""
    await _create_cron(triggers_client, name="q1")
    await _create_cron(triggers_client, name="q2")
    resp = await triggers_client.post(
        "/v1/triggers",
        json={
            "agent_name": "reporter",
            "agent_version": "1.0.0",
            "name": "q3",
            "kind": "cron",
            "config": {"expr": "0 9 * * *"},
        },
    )
    assert resp.status_code == 429
