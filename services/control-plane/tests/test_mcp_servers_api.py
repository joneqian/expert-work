"""API tests for /v1/mcp-servers — Stream V-C.

Tests probe→persist→encrypt, probe-fail→422, SSRF→422, non-admin→403,
duplicate-name→409, and delete-unreferenced→204.

Fixture helpers mirror the pattern from test_platform_config_api.py and
test_members_api.py: create_app with test settings + jwt_verifier, then embed
role in the JWT claim (no role-binding seeding needed for tenant-scope roles).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.mcp_probe import McpProbeError
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle
from orchestrator.tools.mcp import MCPToolDef
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

# ---------------------------------------------------------------------------
# Fake probe callables — injected via monkeypatch
# ---------------------------------------------------------------------------


async def _fake_probe_ok(**kwargs: object) -> list[MCPToolDef]:
    return [MCPToolDef(name="create_issue", description="", input_schema={})]


async def _fake_probe_fail(**kwargs: object) -> list[MCPToolDef]:
    raise McpProbeError("MCP_SERVER_PROBE_FAILED", "connection refused")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _build_settings() -> Settings:
    return Settings(
        service_name="control_plane_test",
        env="dev",
        auth_mode="dev",
        db_dsn="postgresql+asyncpg://test@localhost/test",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


async def _make_app_with_admin() -> tuple[object, dict[str, str], UUID]:
    """Build an in-memory control-plane app and return (app, admin_headers, tenant_id).

    No role-binding seeding is required: the JWT ``roles`` claim carries
    ``("admin",)`` which the RBAC layer reads directly (same pattern as
    test_members_api.py / test_agents_api.py).
    """
    lifecycle = Lifecycle()
    lifecycle.mark_ready()
    settings = _build_settings()
    jwt_verifier = build_test_jwt_verifier()
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=jwt_verifier)
    tenant_id = uuid4()
    token = make_test_jwt(tenant_id=tenant_id, subject=str(uuid4()), roles=("admin",))
    admin_headers = {"Authorization": f"Bearer {token}"}
    return app, admin_headers, tenant_id


async def _seed_viewer_headers(app: object, tenant_id: UUID) -> dict[str, str]:
    """Return headers for a viewer-role principal on the same tenant.

    We accept the tenant_id explicitly so the viewer is scoped to the same
    tenant as the admin created by _make_app_with_admin.
    """
    token = make_test_jwt(tenant_id=tenant_id, subject=str(uuid4()), roles=("viewer",))
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_probes_persists_and_encrypts_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST probe→persist→encrypt: token not in response, resolvable in secret store."""
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github",
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "auth_type": "bearer",
                "token": "ghp_REALTOKEN",
                "timeout_s": 30.0,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "github"
        assert body["data"]["tool_count"] == 1
        # The raw token must not appear in the response body
        assert "ghp_REALTOKEN" not in resp.text
        # token_secret_ref is stripped from the public payload
        assert "token_secret_ref" not in body["data"]
        # The raw token IS resolvable from the secret store under the tenant path
        ref_name = f"helix-agent/tenant/{tenant_id}/mcp/github/token"
        resolved = await app.state.secret_store.get(ref_name)  # type: ignore[attr-defined]
        assert resolved == "ghp_REALTOKEN"


@pytest.mark.asyncio
async def test_post_probe_failure_does_not_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST probe-fail → 422 + nothing persisted."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_fail)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "down",
                "transport": "sse",
                "url": "https://down.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_PROBE_FAILED"
        # Nothing persisted — list returns empty
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.status_code == 200
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_post_ssrf_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST with a link-local/SSRF URL → 422 MCP_SERVER_INVALID_URL before probe."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "evil",
                "transport": "streamable_http",
                "url": "http://169.254.169.254/x",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_INVALID_URL"


@pytest.mark.asyncio
async def test_non_admin_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-admin (viewer) → 403 on POST."""
    app, _, tenant_id = await _make_app_with_admin()
    viewer_headers = await _seed_viewer_headers(app, tenant_id)
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "x",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_name_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registering the same name twice → 409 MCP_SERVER_DUPLICATE on second POST."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        payload = {
            "name": "github",
            "transport": "sse",
            "url": "https://x.example.com/sse",
            "auth_type": "none",
        }
        first = await client.post("/v1/mcp-servers", json=payload, headers=admin_headers)
        assert first.status_code == 201
        dup = await client.post("/v1/mcp-servers", json=payload, headers=admin_headers)
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "MCP_SERVER_DUPLICATE"


@pytest.mark.asyncio
async def test_post_none_auth_with_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST auth_type='none' + token set → 422 MCP_SERVER_TOKEN_NOT_ALLOWED; nothing persisted."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "noauth",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
                "token": "x",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_TOKEN_NOT_ALLOWED"
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_post_bearer_empty_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST auth_type='bearer' + empty token → 422 MCP_SERVER_TOKEN_REQUIRED; nothing persisted."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "bearer-empty",
                "transport": "streamable_http",
                "url": "https://x.example.com/mcp",
                "auth_type": "bearer",
                "token": "",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_TOKEN_REQUIRED"
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_post_invalid_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST name='Bad Name!' (fails pattern) → 422 request-validation error; nothing persisted."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "Bad Name!",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_delete_succeeds_when_unreferenced(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE an existing server that is not referenced by any agent → 204."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        create_resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert create_resp.status_code == 201
        delete_resp = await client.delete("/v1/mcp-servers/github", headers=admin_headers)
        assert delete_resp.status_code == 204
        # Verify it's gone from the list
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_post_and_delete_invalidate_tenant_mcp_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST and DELETE both call pool_service.invalidate + agent_runtime.invalidate_tenant."""
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)

    class _PoolSpy:
        def __init__(self) -> None:
            self.invalidated: list[UUID] = []

        async def invalidate(self, tid: UUID) -> None:
            self.invalidated.append(tid)

    class _RuntimeSpy:
        def __init__(self) -> None:
            self.invalidated: list[UUID] = []

        def invalidate_tenant(self, tid: UUID) -> None:
            self.invalidated.append(tid)

    pool_spy = _PoolSpy()
    rt_spy = _RuntimeSpy()
    app.state.tenant_mcp_pool_service = pool_spy
    app.state.agent_runtime = rt_spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        post_resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert post_resp.status_code == 201
        delete_resp = await client.delete("/v1/mcp-servers/github", headers=admin_headers)
        assert delete_resp.status_code == 204

    assert pool_spy.invalidated.count(tenant_id) == 2
    assert rt_spy.invalidated.count(tenant_id) == 2


@pytest.mark.asyncio
async def test_test_connection_probes_without_persisting(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /test probes the connection and returns tool_count — nothing is persisted."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.post(
            "/v1/mcp-servers/test",
            json={
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["tool_count"] == 1
        # nothing persisted
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"] == []


@pytest.mark.asyncio
async def test_test_connection_failure_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /test with a failing probe → 422 MCP_SERVER_PROBE_FAILED."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_fail)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.post(
            "/v1/mcp-servers/test",
            json={"transport": "sse", "url": "https://down.example.com/sse", "auth_type": "none"},
            headers=admin_headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "MCP_SERVER_PROBE_FAILED"


@pytest.mark.asyncio
async def test_available_lists_tenant_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /available returns tenant-registered servers with source='tenant'.

    Platform-allowlist seeding requires a tenant_config row which is not seeded
    in the basic in-memory harness; that half is covered by the tenant_config
    service unit tests. This test asserts the tenant-server half only.
    """
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        r = await client.get("/v1/mcp-servers/available", headers=admin_headers)
        assert r.status_code == 200
        names = {item["name"] for item in r.json()["data"]}
        assert "github" in names
        sources = {item["name"]: item["source"] for item in r.json()["data"]}
        assert sources["github"] == "tenant"


@pytest.mark.asyncio
async def test_server_tools_lists_live_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /{name}/tools returns the live tool list via probe."""
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        r = await client.get("/v1/mcp-servers/github/tools", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["data"][0]["name"] == "create_issue"


@pytest.mark.asyncio
async def test_server_tools_unknown_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /{name}/tools for an unregistered server → 404 MCP_SERVER_NOT_FOUND."""
    app, admin_headers, _ = await _make_app_with_admin()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r = await client.get("/v1/mcp-servers/nope/tools", headers=admin_headers)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_invalidates_tenant_mcp_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH calls pool_service.invalidate + agent_runtime.invalidate_tenant."""
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)

    class _PoolSpy:
        def __init__(self) -> None:
            self.invalidated: list[UUID] = []

        async def invalidate(self, tid: UUID) -> None:
            self.invalidated.append(tid)

    class _RuntimeSpy:
        def __init__(self) -> None:
            self.invalidated: list[UUID] = []

        def invalidate_tenant(self, tid: UUID) -> None:
            self.invalidated.append(tid)

    pool_spy = _PoolSpy()
    rt_spy = _RuntimeSpy()
    app.state.tenant_mcp_pool_service = pool_spy
    app.state.agent_runtime = rt_spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        # Register first
        post_resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "github",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert post_resp.status_code == 201
        # Reset spy counts after POST
        pool_spy.invalidated.clear()
        rt_spy.invalidated.clear()
        # PATCH to disable
        patch_resp = await client.patch(
            "/v1/mcp-servers/github",
            json={"enabled": False},
            headers=admin_headers,
        )
        assert patch_resp.status_code == 200

    assert pool_spy.invalidated.count(tenant_id) == 1
    assert rt_spy.invalidated.count(tenant_id) == 1


@pytest.mark.asyncio
async def test_probe_endpoints_have_dedicated_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit #6: probe-bearing endpoints draw on a tight dedicated bucket — a
    second probe past the (capacity-1) bucket is 429'd before the outbound call."""
    lifecycle = Lifecycle()
    lifecycle.mark_ready()
    settings = Settings(
        service_name="control_plane_test",
        env="dev",
        auth_mode="dev",
        db_dsn="postgresql+asyncpg://test@localhost/test",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
        # Tight probe bucket: 1 token, near-zero refill so the 2nd call is denied.
        mcp_probe_rate_limit_capacity=1,
        mcp_probe_rate_limit_refill_per_sec=0.001,
    )
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=build_test_jwt_verifier())
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    token = make_test_jwt(tenant_id=uuid4(), subject=str(uuid4()), roles=("admin",))
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "auth_type": "none",
        "timeout_s": 30.0,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        r1 = await client.post("/v1/mcp-servers/test", json=body, headers=headers)
        r2 = await client.post("/v1/mcp-servers/test", json=body, headers=headers)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 429
    assert r2.json()["detail"]["code"] == "MCP_PROBE_RATE_LIMITED"
