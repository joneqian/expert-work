"""API tests for the MCP connector catalog — Stream W / platform-server model.

Covers ``GET /v1/mcp-servers/catalog`` (browse + entitlement) and the custom
kill-switch on ``POST ""``. The legacy ``POST
/v1/mcp-servers/catalog/{catalog_id}/instances`` flow (tenant fills auth_schema
fields) was retired with the platform-server redesign (P4) — tenants now opt
into fully-configured platform servers via the enable/disable endpoints
(covered in test_mcp_servers_api.py).

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
from control_plane.settings import Settings
from control_plane.tenant_scope import bypass_rls_session
from expert_work.common.lifecycle import Lifecycle
from expert_work.persistence import InMemoryMcpConnectorCatalogStore
from expert_work.protocol import (
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


@dataclass(frozen=True)
class _StubConfig:
    plan: TenantPlan
    allow_custom_mcp_servers: bool
    mcp_allowlist: tuple[str, ...] = ()


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
    url_template: str = "https://mcp.example.com/sse",
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
        # Opt-in selection state (P2/P4) — nothing enabled until selected.
        assert by_name["freecon"]["tenant_enabled"] is False


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
