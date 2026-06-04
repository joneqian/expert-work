"""API tests for the MCP connector catalog instantiation flow — Stream W-4.

Covers ``GET /v1/mcp-servers/catalog`` (browse + entitlement), ``POST
/v1/mcp-servers/catalog/{catalog_id}/instances`` (tier gate, field validation,
url-template resolution, SSRF, duplicate, probe, secret-ref persistence),
``/available`` catalog enrichment, and the custom kill-switch on ``POST ""``.

Fixture helpers mirror test_mcp_servers_api.py: create_app with test settings +
jwt_verifier, role embedded in the JWT ``roles`` claim, and ``probe_remote_mcp``
stubbed via monkeypatch. The catalog store and a tenant_config_service stub are
attached to ``app.state`` after construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.mcp_probe import McpProbeError
from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.persistence import InMemoryMcpConnectorCatalogStore
from helix_agent.protocol import (
    McpConnectorAuthField,
    McpConnectorAuthSchema,
    McpConnectorCatalogRecord,
    McpConnectorCatalogUpsert,
    TenantPlan,
)
from orchestrator.tools.mcp import MCPToolDef
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

# ---------------------------------------------------------------------------
# Fake probe + tenant_config_service stubs
# ---------------------------------------------------------------------------


async def _fake_probe_ok(**kwargs: object) -> list[MCPToolDef]:
    return [MCPToolDef(name="create_issue", description="", input_schema={})]


async def _fake_probe_fail(**kwargs: object) -> list[MCPToolDef]:
    raise McpProbeError("MCP_SERVER_PROBE_FAILED", "connection refused")


@dataclass(frozen=True)
class _StubConfig:
    plan: TenantPlan
    allow_custom_mcp_servers: bool


class _StubTenantConfigService:
    def __init__(self, *, plan: TenantPlan, allow_custom: bool) -> None:
        self._cfg = _StubConfig(plan=plan, allow_custom_mcp_servers=allow_custom)

    async def get(self, *, tenant_id: UUID) -> _StubConfig:
        return self._cfg


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
    lifecycle = Lifecycle()
    lifecycle.mark_ready()
    settings = _build_settings()
    jwt_verifier = build_test_jwt_verifier()
    app = create_app(settings=settings, lifecycle=lifecycle, jwt_verifier=jwt_verifier)
    tenant_id = uuid4()
    token = make_test_jwt(tenant_id=tenant_id, subject=str(uuid4()), roles=("admin",))
    admin_headers = {"Authorization": f"Bearer {token}"}
    return app, admin_headers, tenant_id


async def _wire_catalog(
    app: object, *, plan: TenantPlan = TenantPlan.FREE, allow_custom: bool = True
) -> InMemoryMcpConnectorCatalogStore:
    store = InMemoryMcpConnectorCatalogStore()
    app.state.mcp_connector_catalog_store = store  # type: ignore[attr-defined]
    app.state.tenant_config_service = _StubTenantConfigService(  # type: ignore[attr-defined]
        plan=plan, allow_custom=allow_custom
    )
    return store


async def _seed_entry(
    store: InMemoryMcpConnectorCatalogStore,
    *,
    name: str,
    required_tier: TenantPlan = TenantPlan.FREE,
    auth_type: str = "none",
    url_template: str = "https://mcp.example.com/{org}/sse",
    fields: list[McpConnectorAuthField] | None = None,
    enabled: bool = True,
) -> McpConnectorCatalogRecord:
    upsert = McpConnectorCatalogUpsert(
        name=name,
        display_name=name.title(),
        description=f"{name} connector",
        category="dev",
        transport="sse",
        url_template=url_template,
        auth_type=auth_type,  # type: ignore[arg-type]
        auth_schema=McpConnectorAuthSchema(fields=fields or []),
        required_tier=required_tier,
        enabled=enabled,
    )
    async with bypass_rls_session():
        return await store.create(upsert=upsert, actor_id="seed")


# ---------------------------------------------------------------------------
# GET /catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_list_reports_entitlement() -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    await _seed_entry(store, name="freecon", required_tier=TenantPlan.FREE)
    await _seed_entry(store, name="procon", required_tier=TenantPlan.PRO)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.get("/v1/mcp-servers/catalog", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        entitled = {e["name"]: e["entitled"] for e in resp.json()["data"]}
        assert entitled == {"freecon": True, "procon": False}
        by_name = {e["name"]: e for e in resp.json()["data"]}
        assert by_name["procon"]["required_tier"] == "pro"
        assert "auth_schema" in by_name["freecon"]


# ---------------------------------------------------------------------------
# POST instantiate — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instantiate_none_auth_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store,
        name="github",
        url_template="https://mcp.example.com/{org}/sse",
        fields=[McpConnectorAuthField(key="org", label="Org", kind="param")],
    )

    class _PoolSpy:
        def __init__(self) -> None:
            self.invalidated: list[UUID] = []

        async def invalidate(self, tid: UUID) -> None:
            self.invalidated.append(tid)

    pool_spy = _PoolSpy()
    app.state.tenant_mcp_pool_service = pool_spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances",
            json={"params": {"org": "acme"}},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["name"] == "github"
        assert data["url"] == "https://mcp.example.com/acme/sse"
        assert data["catalog_id"] == str(entry.id)
        assert data["tool_count"] == 1
        # listed as a tenant row
        lst = await client.get("/v1/mcp-servers", headers=admin_headers)
        assert lst.json()["data"][0]["name"] == "github"
    assert pool_spy.invalidated.count(tenant_id) == 1


@pytest.mark.asyncio
async def test_instantiate_bearer_stores_secret_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, tenant_id = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store,
        name="linear",
        auth_type="bearer",
        url_template="https://mcp.example.com/mcp",
        fields=[McpConnectorAuthField(key="token", label="Token", kind="secret")],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances",
            json={"secrets": {"token": "lin_REALTOKEN"}},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        assert "lin_REALTOKEN" not in resp.text
        assert "token_secret_ref" not in resp.json()["data"]
    ref_name = f"helix-agent/tenant/{tenant_id}/mcp/linear/token"
    resolved = await app.state.secret_store.get(ref_name)  # type: ignore[attr-defined]
    assert resolved == "lin_REALTOKEN"


# ---------------------------------------------------------------------------
# POST instantiate — gates / validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instantiate_tier_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store, name="procon", required_tier=TenantPlan.PRO, url_template="https://m.test/mcp"
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_TIER_REQUIRED"


@pytest.mark.asyncio
async def test_instantiate_missing_required_field(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store,
        name="github",
        url_template="https://m.test/{org}/sse",
        fields=[McpConnectorAuthField(key="org", label="Org", kind="param")],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_FIELD_MISSING"


@pytest.mark.asyncio
async def test_instantiate_unknown_field(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store,
        name="github",
        url_template="https://m.test/{org}/sse",
        fields=[McpConnectorAuthField(key="org", label="Org", kind="param")],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances",
            json={"params": {"org": "acme", "bogus": "x"}},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_FIELD_UNKNOWN"


@pytest.mark.asyncio
async def test_instantiate_url_template_missing_param(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    # template references {org} but declares no param field for it (optional org param
    # not supplied) → url-template resolution fails.
    entry = await _seed_entry(
        store,
        name="github",
        url_template="https://m.test/{org}/sse",
        fields=[McpConnectorAuthField(key="org", label="Org", kind="param", required=False)],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_URL_TEMPLATE"


@pytest.mark.asyncio
async def test_instantiate_ssrf_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store,
        name="evil",
        url_template="http://{host}/x",
        fields=[McpConnectorAuthField(key="host", label="Host", kind="param")],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances",
            json={"params": {"host": "169.254.169.254"}},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_SERVER_INVALID_URL"


@pytest.mark.asyncio
async def test_instantiate_param_host_pivot_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """W-7: a param value with URL-structural chars must NOT pivot the host.

    ``org="evil.com/"`` against ``https://{org}.example.com/mcp`` would otherwise
    resolve to host ``evil.com`` (passes the private-IP SSRF guard) and exfiltrate
    the bearer token. The structural-char reject must catch it BEFORE the probe.
    """
    app, admin_headers, _ = await _make_app_with_admin()

    async def _probe_must_not_run(**_kwargs: object) -> list[object]:
        raise AssertionError("probe must not run for a rejected param value")

    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _probe_must_not_run)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(
        store,
        name="sub",
        url_template="https://{org}.example.com/mcp",
        fields=[McpConnectorAuthField(key="org", label="Org", kind="param")],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances",
            json={"params": {"org": "evil.com/"}},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_PARAM_INVALID"


@pytest.mark.asyncio
async def test_instantiate_duplicate_name(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(store, name="github", url_template="https://m.test/mcp")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        first = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert first.status_code == 201
        dup = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "MCP_SERVER_DUPLICATE"


@pytest.mark.asyncio
async def test_instantiate_catalog_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    await _wire_catalog(app, plan=TenantPlan.FREE)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{uuid4()}/instances", json={}, headers=admin_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_NOT_FOUND"


@pytest.mark.asyncio
async def test_instantiate_disabled_entry_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(store, name="off", url_template="https://m.test/mcp", enabled=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "MCP_CATALOG_NOT_FOUND"


# ---------------------------------------------------------------------------
# /available enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_available_shows_catalog_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE)
    entry = await _seed_entry(store, name="github", url_template="https://m.test/mcp")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        inst = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert inst.status_code == 201
        avail = await client.get("/v1/mcp-servers/available", headers=admin_headers)
        assert avail.status_code == 200
        rows = {r["name"]: r for r in avail.json()["data"] if r["source"] == "tenant"}
        assert rows["github"]["catalog_id"] == str(entry.id)
        assert rows["github"]["catalog_name"] == "github"


# ---------------------------------------------------------------------------
# Custom kill-switch (W-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_registration_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    await _wire_catalog(app, plan=TenantPlan.FREE, allow_custom=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            "/v1/mcp-servers",
            json={
                "name": "custom",
                "transport": "sse",
                "url": "https://x.example.com/sse",
                "auth_type": "none",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "MCP_CUSTOM_DISABLED"


@pytest.mark.asyncio
async def test_instantiation_works_when_custom_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app, admin_headers, _ = await _make_app_with_admin()
    monkeypatch.setattr("control_plane.api.mcp_servers.probe_remote_mcp", _fake_probe_ok)
    store = await _wire_catalog(app, plan=TenantPlan.FREE, allow_custom=False)
    entry = await _seed_entry(store, name="github", url_template="https://m.test/mcp")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        resp = await client.post(
            f"/v1/mcp-servers/catalog/{entry.id}/instances", json={}, headers=admin_headers
        )
        assert resp.status_code == 201
