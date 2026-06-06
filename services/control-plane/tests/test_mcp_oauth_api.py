"""API tests for /v1/mcp-oauth initiate + callback — Stream MCP-OAUTH (OA-3a).

The OAuth engine (discover/exchange) is monkeypatched — no real network.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.mcp_oauth import OAuthServerMetadata, TokenResponse
from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.protocol import McpConnectorAuthSchema, McpConnectorCatalogUpsert
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_REDIRECT = "https://app.test/v1/mcp-oauth/callback"
_META = OAuthServerMetadata(
    authorization_endpoint="https://auth.linear.test/authorize",
    token_endpoint="https://auth.linear.test/token",
    resource="https://mcp.linear.app/sse",
    scopes_supported=("read", "write"),
)


def _settings(*, redirect_uri: str | None = _REDIRECT) -> Settings:
    return Settings(
        service_name="control_plane_test",
        env="dev",
        auth_mode="dev",
        db_dsn="postgresql+asyncpg://test@localhost/test",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        mcp_oauth_redirect_uri=redirect_uri,
    )


async def _make_app(
    *, redirect_uri: str | None = _REDIRECT
) -> tuple[object, dict[str, str], UUID, str]:
    lifecycle = Lifecycle()
    lifecycle.mark_ready()
    app = create_app(
        settings=_settings(redirect_uri=redirect_uri),
        lifecycle=lifecycle,
        jwt_verifier=build_test_jwt_verifier(),
    )
    tenant_id = uuid4()
    user_id = str(uuid4())
    token = make_test_jwt(tenant_id=tenant_id, subject=user_id, roles=("admin",))
    return app, {"Authorization": f"Bearer {token}"}, tenant_id, user_id


async def _seed_oauth2_entry(app: object, *, name: str = "linear") -> UUID:
    upsert = McpConnectorCatalogUpsert(
        name=name,
        display_name="Linear",
        transport="sse",
        url_template="https://mcp.linear.app/sse",
        auth_type="oauth2",
        auth_schema=McpConnectorAuthSchema(),
        oauth_client_id="helix-linear-app",
        oauth_scopes="read",
    )
    async with bypass_rls_session():
        rec = await app.state.mcp_connector_catalog_store.create(  # type: ignore[attr-defined]
            upsert=upsert, actor_id="seed"
        )
    return rec.id


async def _seed_none_entry(app: object, *, name: str = "plain") -> UUID:
    upsert = McpConnectorCatalogUpsert(
        name=name,
        display_name="Plain",
        transport="sse",
        url_template="https://mcp.plain.test/sse",
        auth_type="none",
    )
    async with bypass_rls_session():
        rec = await app.state.mcp_connector_catalog_store.create(  # type: ignore[attr-defined]
            upsert=upsert, actor_id="seed"
        )
    return rec.id


async def _fake_discover(**_kwargs: object) -> OAuthServerMetadata:
    return _META


async def _fake_exchange(**_kwargs: object) -> TokenResponse:
    return TokenResponse(access_token="AT", refresh_token="RT", expires_in=3600, scope="read")


@pytest.mark.asyncio
async def test_initiate_returns_authorize_url(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers, tenant_id, user_id = await _make_app()
    monkeypatch.setattr("control_plane.api.mcp_oauth_api.discover_oauth_metadata", _fake_discover)
    cat_id = await _seed_oauth2_entry(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{cat_id}/oauth/initiate", headers=headers
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["authorize_url"].startswith("https://auth.linear.test/authorize?")
    assert "client_id=helix-linear-app" in body["authorize_url"]
    # A pending connection now exists for this user+connector.
    conn = await app.state.mcp_oauth_connection_store.get_for_connector(  # type: ignore[attr-defined]
        tenant_id=tenant_id, user_id=user_id, catalog_id=cat_id
    )
    assert conn is not None and conn.status == "pending" and conn.pkce_verifier


@pytest.mark.asyncio
async def test_full_oauth_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers, tenant_id, user_id = await _make_app()
    monkeypatch.setattr("control_plane.api.mcp_oauth_api.discover_oauth_metadata", _fake_discover)
    monkeypatch.setattr("control_plane.api.mcp_oauth_api.exchange_code", _fake_exchange)
    cat_id = await _seed_oauth2_entry(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        init = await client.post(
            f"/v1/mcp-servers/catalog/{cat_id}/oauth/initiate", headers=headers
        )
        assert init.status_code == 201
        conn = await app.state.mcp_oauth_connection_store.get_for_connector(  # type: ignore[attr-defined]
            tenant_id=tenant_id, user_id=user_id, catalog_id=cat_id
        )
        state = conn.oauth_state
        cb = await client.get(
            "/v1/mcp-oauth/callback", params={"state": state, "code": "authcode"}, headers=headers
        )
    assert cb.status_code == 200, cb.text
    assert cb.json()["status"] == "connected"
    # Token persisted to the secret store; connection marked connected + flow cleared.
    final = await app.state.mcp_oauth_connection_store.get_for_connector(  # type: ignore[attr-defined]
        tenant_id=tenant_id, user_id=user_id, catalog_id=cat_id
    )
    assert final.status == "connected"
    assert final.access_token_ref is not None
    assert final.oauth_state is None and final.pkce_verifier is None
    stored = await app.state.secret_store.get(  # type: ignore[attr-defined]
        final.access_token_ref.removeprefix("secret://")
    )
    assert stored == "AT"


@pytest.mark.asyncio
async def test_callback_bad_state_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    app, headers, _, _ = await _make_app()
    monkeypatch.setattr("control_plane.api.mcp_oauth_api.discover_oauth_metadata", _fake_discover)
    cat_id = await _seed_oauth2_entry(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post(f"/v1/mcp-servers/catalog/{cat_id}/oauth/initiate", headers=headers)
        cb = await client.get(
            "/v1/mcp-oauth/callback", params={"state": "wrong", "code": "x"}, headers=headers
        )
    assert cb.status_code == 400
    assert cb.json()["detail"]["code"] == "MCP_OAUTH_STATE_INVALID"


@pytest.mark.asyncio
async def test_initiate_non_oauth2_entry_rejected() -> None:
    app, headers, _, _ = await _make_app()
    cat_id = await _seed_none_entry(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{cat_id}/oauth/initiate", headers=headers
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "MCP_CATALOG_NOT_OAUTH"


@pytest.mark.asyncio
async def test_initiate_not_configured_returns_503() -> None:
    app, headers, _, _ = await _make_app(redirect_uri=None)
    cat_id = await _seed_oauth2_entry(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{cat_id}/oauth/initiate", headers=headers
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "MCP_OAUTH_NOT_CONFIGURED"
