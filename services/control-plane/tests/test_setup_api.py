"""Endpoint tests for the first-run setup wizard — Stream ACCT (Mini-ADR ACCT-2).

The wizard stands up the first platform ``system_admin`` over an
unauthenticated ``POST /v1/setup``, gated by the setup token + the zero-admin
invariant. Uses a Fake Keycloak so the flow runs without a live IdP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.auth import JWTVerifier
from control_plane.keycloak import FakeKeycloakAdminClient
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle

SETUP_TOKEN = "s3cret-deploy-token"


def _body() -> dict[str, object]:
    return {
        "admin_email": "founder@corp.com",
        "admin_password": "correct horse battery",
        "admin_display_name": "The Founder",
        "platform_tenant_display_name": "Platform",
    }


async def _make_client(
    settings: Settings,
    lifecycle: Lifecycle,
    jwt_verifier: JWTVerifier,
    *,
    setup_token: str | None,
) -> tuple[AsyncClient, object, FakeKeycloakAdminClient]:
    kc = FakeKeycloakAdminClient()
    app = create_app(
        settings=settings.model_copy(update={"setup_token": setup_token}),
        lifecycle=lifecycle,
        jwt_verifier=jwt_verifier,
        keycloak_admin_client=kc,
    )
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://control-plane.test")
    return client, app, kc


@pytest.fixture
async def setup_ctx(
    settings: Settings, lifecycle: Lifecycle, jwt_verifier: JWTVerifier
) -> AsyncIterator[tuple[AsyncClient, object, FakeKeycloakAdminClient]]:
    client, app, kc = await _make_client(settings, lifecycle, jwt_verifier, setup_token=SETUP_TOKEN)
    async with client:
        yield client, app, kc


@pytest.mark.asyncio
async def test_status_uninitialized_then_initialized(
    setup_ctx: tuple[AsyncClient, object, FakeKeycloakAdminClient],
) -> None:
    client, _app, _kc = setup_ctx
    r = await client.get("/v1/setup/status")
    assert r.status_code == 200
    assert r.json()["data"] == {"initialized": False, "setup_enabled": True}

    ok = await client.post("/v1/setup", json=_body(), headers={"X-Setup-Token": SETUP_TOKEN})
    assert ok.status_code == 200, ok.text

    r2 = await client.get("/v1/setup/status")
    assert r2.json()["data"]["initialized"] is True


@pytest.mark.asyncio
async def test_missing_token_rejected(
    setup_ctx: tuple[AsyncClient, object, FakeKeycloakAdminClient],
) -> None:
    client, app, _kc = setup_ctx
    r = await client.post("/v1/setup", json=_body())
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "INVALID_SETUP_TOKEN"
    # nothing provisioned
    assert await app.state.role_binding_repo.list_platform_scope() == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_wrong_token_rejected(
    setup_ctx: tuple[AsyncClient, object, FakeKeycloakAdminClient],
) -> None:
    client, _app, _kc = setup_ctx
    r = await client.post("/v1/setup", json=_body(), headers={"X-Setup-Token": "nope"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "INVALID_SETUP_TOKEN"


@pytest.mark.asyncio
async def test_happy_path_provisions_tenant_user_binding(
    setup_ctx: tuple[AsyncClient, object, FakeKeycloakAdminClient],
) -> None:
    client, app, kc = setup_ctx
    r = await client.post("/v1/setup", json=_body(), headers={"X-Setup-Token": SETUP_TOKEN})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    subject_id = data["subject_id"]

    # platform-scope system_admin binding for the new subject
    bindings = await app.state.role_binding_repo.list_platform_scope()  # type: ignore[attr-defined]
    assert [str(b.subject_id) for b in bindings] == [subject_id]

    # Keycloak account: verified + password set (non-temporary)
    stored = kc.users[subject_id]
    assert stored.email_verified is True
    assert kc.password_resets == [(subject_id, "correct horse battery", False)]


@pytest.mark.asyncio
async def test_second_run_conflicts_after_initialized(
    setup_ctx: tuple[AsyncClient, object, FakeKeycloakAdminClient],
) -> None:
    client, _app, _kc = setup_ctx
    first = await client.post("/v1/setup", json=_body(), headers={"X-Setup-Token": SETUP_TOKEN})
    assert first.status_code == 200
    second = await client.post(
        "/v1/setup",
        json={**_body(), "admin_email": "other@corp.com"},
        headers={"X-Setup-Token": SETUP_TOKEN},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "ALREADY_INITIALIZED"


@pytest.mark.asyncio
async def test_keycloak_admin_secret_missing_returns_clean_502(
    settings: Settings, lifecycle: Lifecycle, jwt_verifier: JWTVerifier
) -> None:
    """Missing KC admin secret (deploy prerequisite) → 502, not a bare 500."""
    from helix_agent.runtime.secret_store.base import SecretNotFoundError

    class _NoSecretKeycloak(FakeKeycloakAdminClient):
        async def create_user(self, **_kwargs: object) -> object:  # type: ignore[override]
            raise SecretNotFoundError("helix-agent/platform/keycloak/admin-client-secret")

    kc = _NoSecretKeycloak()
    app = create_app(
        settings=settings.model_copy(update={"setup_token": SETUP_TOKEN}),
        lifecycle=lifecycle,
        jwt_verifier=jwt_verifier,
        keycloak_admin_client=kc,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        r = await client.post("/v1/setup", json=_body(), headers={"X-Setup-Token": SETUP_TOKEN})
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "KEYCLOAK_ADMIN_SECRET_MISSING"


@pytest.mark.asyncio
async def test_setup_disabled_when_token_unset(
    settings: Settings, lifecycle: Lifecycle, jwt_verifier: JWTVerifier
) -> None:
    client, _app, _kc = await _make_client(settings, lifecycle, jwt_verifier, setup_token=None)
    async with client:
        status = await client.get("/v1/setup/status")
        assert status.json()["data"]["setup_enabled"] is False
        r = await client.post("/v1/setup", json=_body(), headers={"X-Setup-Token": "anything"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "SETUP_NOT_CONFIGURED"
