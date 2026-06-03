"""API tests for /v1/platform/mcp-catalog — Stream W (W-3).

Covers the system_admin CRUD surface, the platform-scope gating (tenant admin →
403), duplicate / not-found mappings, the Upsert + merged-record validators
(422), and the FK-RESTRICT in-use delete (409 CATALOG_IN_USE) via a fake store.

Fixtures mirror test_platform_config_api.py: system_admin is established by
seeding a SYSTEM_ADMIN role binding (the middleware augments the principal with
``is_system_admin``); a plain ``admin`` JWT stands in for a tenant admin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.persistence import McpConnectorCatalogInUseError
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import AuditQuery, Role
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)


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


def _valid_entry(name: str = "github", *, category: str = "vcs") -> dict[str, object]:
    return {
        "name": name,
        "display_name": "GitHub",
        "description": "Official GitHub connector",
        "category": category,
        "transport": "streamable_http",
        "url_template": "https://mcp.github.com/mcp",
        "auth_type": "bearer",
        "auth_schema": {
            "fields": [{"key": "token", "label": "API Token", "kind": "secret", "required": True}]
        },
        "required_tier": "free",
    }


class _Ctx:
    def __init__(
        self,
        client: AsyncClient,
        audit_store: InMemoryAuditLogStore,
        app: object,
        admin_tenant: UUID,
        admin_headers: dict[str, str],
        tenant_admin_headers: dict[str, str],
    ) -> None:
        self.client = client
        self.audit_store = audit_store
        self.app = app
        self.admin_tenant = admin_tenant
        self.admin_headers = admin_headers
        self.tenant_admin_headers = tenant_admin_headers


@pytest.fixture
async def ctx() -> AsyncIterator[_Ctx]:
    lifecycle = Lifecycle()
    lifecycle.mark_ready()
    audit_store = InMemoryAuditLogStore()
    app = create_app(
        settings=_build_settings(),
        lifecycle=lifecycle,
        jwt_verifier=build_test_jwt_verifier(),
        audit_logger=build_default_audit_logger(audit_store),
    )
    # Seed a SYSTEM_ADMIN role binding so the middleware sets is_system_admin.
    sys_admin_id = uuid4()
    await app.state.role_binding_repo.create(  # type: ignore[attr-defined]
        subject_type="user",
        subject_id=sys_admin_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="seed",
    )
    admin_tenant = uuid4()
    admin_jwt = make_test_jwt(tenant_id=admin_tenant, subject=str(sys_admin_id))
    admin_headers = {"Authorization": f"Bearer {admin_jwt}"}
    # A plain tenant admin (no platform scope) — has mcp_catalog RBAC via ADMIN
    # but must be rejected by the inline is_system_admin check.
    tenant_admin_jwt = make_test_jwt(tenant_id=uuid4(), subject=str(uuid4()), roles=("admin",))
    tenant_admin_headers = {"Authorization": f"Bearer {tenant_admin_jwt}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        yield _Ctx(client, audit_store, app, admin_tenant, admin_headers, tenant_admin_headers)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_get_list_patch_delete(ctx: _Ctx) -> None:
    # create → 201
    create = await ctx.client.post(
        "/v1/platform/mcp-catalog", json=_valid_entry(), headers=ctx.admin_headers
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["success"] is True
    assert body["data"]["name"] == "github"
    catalog_id = body["data"]["id"]

    # get_by_id reflects it
    got = await ctx.client.get(f"/v1/platform/mcp-catalog/{catalog_id}", headers=ctx.admin_headers)
    assert got.status_code == 200
    assert got.json()["data"]["display_name"] == "GitHub"

    # list reflects it
    lst = await ctx.client.get("/v1/platform/mcp-catalog", headers=ctx.admin_headers)
    assert lst.status_code == 200
    names = {r["name"] for r in lst.json()["data"]}
    assert "github" in names

    # patch changes a field
    patch = await ctx.client.patch(
        f"/v1/platform/mcp-catalog/{catalog_id}",
        json={"display_name": "GitHub (official)", "enabled": False},
        headers=ctx.admin_headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["data"]["display_name"] == "GitHub (official)"
    assert patch.json()["data"]["enabled"] is False

    # delete → 204
    delete = await ctx.client.delete(
        f"/v1/platform/mcp-catalog/{catalog_id}", headers=ctx.admin_headers
    )
    assert delete.status_code == 204
    gone = await ctx.client.get(f"/v1/platform/mcp-catalog/{catalog_id}", headers=ctx.admin_headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_list_category_filter(ctx: _Ctx) -> None:
    await ctx.client.post(
        "/v1/platform/mcp-catalog",
        json=_valid_entry("github", category="vcs"),
        headers=ctx.admin_headers,
    )
    await ctx.client.post(
        "/v1/platform/mcp-catalog",
        json=_valid_entry("postgres", category="database"),
        headers=ctx.admin_headers,
    )
    lst = await ctx.client.get(
        "/v1/platform/mcp-catalog", params={"category": "vcs"}, headers=ctx.admin_headers
    )
    assert lst.status_code == 200
    names = {r["name"] for r in lst.json()["data"]}
    assert names == {"github"}


# ---------------------------------------------------------------------------
# Error mappings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_name_409(ctx: _Ctx) -> None:
    first = await ctx.client.post(
        "/v1/platform/mcp-catalog", json=_valid_entry(), headers=ctx.admin_headers
    )
    assert first.status_code == 201
    dup = await ctx.client.post(
        "/v1/platform/mcp-catalog", json=_valid_entry(), headers=ctx.admin_headers
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "CATALOG_DUPLICATE"


@pytest.mark.asyncio
async def test_get_patch_delete_missing_404(ctx: _Ctx) -> None:
    missing = str(uuid4())
    g = await ctx.client.get(f"/v1/platform/mcp-catalog/{missing}", headers=ctx.admin_headers)
    assert g.status_code == 404
    p = await ctx.client.patch(
        f"/v1/platform/mcp-catalog/{missing}",
        json={"display_name": "x"},
        headers=ctx.admin_headers,
    )
    assert p.status_code == 404
    d = await ctx.client.delete(f"/v1/platform/mcp-catalog/{missing}", headers=ctx.admin_headers)
    assert d.status_code == 404


@pytest.mark.asyncio
async def test_create_invalid_body_422(ctx: _Ctx) -> None:
    """bearer auth_type with no secret field in auth_schema → Upsert validator rejects."""
    bad = _valid_entry()
    bad["auth_schema"] = {"fields": []}  # bearer requires exactly one secret field
    resp = await ctx.client.post("/v1/platform/mcp-catalog", json=bad, headers=ctx.admin_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_violates_merged_invariant_422(ctx: _Ctx) -> None:
    """A bearer entry patched to an auth_schema with no secret field → 422."""
    create = await ctx.client.post(
        "/v1/platform/mcp-catalog", json=_valid_entry(), headers=ctx.admin_headers
    )
    assert create.status_code == 201
    catalog_id = create.json()["data"]["id"]
    resp = await ctx.client.patch(
        f"/v1/platform/mcp-catalog/{catalog_id}",
        json={"auth_schema": {"fields": []}},  # drops the only secret field
        headers=ctx.admin_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "CATALOG_INVALID"


# ---------------------------------------------------------------------------
# Platform gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_admin_forbidden_on_every_endpoint(ctx: _Ctx) -> None:
    h = ctx.tenant_admin_headers
    some_id = str(uuid4())
    # Bind each response before asserting: a request call inside an ``assert``
    # expression is stripped under ``python -O`` (CodeQL py/side-effect-in-assert,
    # [memory:no-side-effect-in-assert]).
    created = await ctx.client.post("/v1/platform/mcp-catalog", json=_valid_entry(), headers=h)
    listed = await ctx.client.get("/v1/platform/mcp-catalog", headers=h)
    got = await ctx.client.get(f"/v1/platform/mcp-catalog/{some_id}", headers=h)
    patched = await ctx.client.patch(
        f"/v1/platform/mcp-catalog/{some_id}", json={"display_name": "x"}, headers=h
    )
    deleted = await ctx.client.delete(f"/v1/platform/mcp-catalog/{some_id}", headers=h)
    assert created.status_code == 403
    assert listed.status_code == 403
    assert got.status_code == 403
    assert patched.status_code == 403
    assert deleted.status_code == 403


# ---------------------------------------------------------------------------
# Delete-in-use → 409 (fake store)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_in_use_409(ctx: _Ctx) -> None:
    create = await ctx.client.post(
        "/v1/platform/mcp-catalog", json=_valid_entry(), headers=ctx.admin_headers
    )
    assert create.status_code == 201
    catalog_id = create.json()["data"]["id"]

    real_store = ctx.app.state.mcp_connector_catalog_store  # type: ignore[attr-defined]

    class _InUseStore:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        async def get_by_id(self, cid: UUID) -> object:
            return await self._inner.get_by_id(cid)

        async def delete(self, cid: UUID) -> None:
            raise McpConnectorCatalogInUseError(catalog_id=cid)

    ctx.app.state.mcp_connector_catalog_store = _InUseStore(real_store)  # type: ignore[attr-defined]
    resp = await ctx.client.delete(
        f"/v1/platform/mcp-catalog/{catalog_id}", headers=ctx.admin_headers
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "CATALOG_IN_USE"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_delete_emit_audit(ctx: _Ctx) -> None:
    create = await ctx.client.post(
        "/v1/platform/mcp-catalog", json=_valid_entry(), headers=ctx.admin_headers
    )
    assert create.status_code == 201
    catalog_id = create.json()["data"]["id"]
    delete = await ctx.client.delete(
        f"/v1/platform/mcp-catalog/{catalog_id}", headers=ctx.admin_headers
    )
    assert delete.status_code == 204

    page = await ctx.audit_store.query(AuditQuery(tenant_id=ctx.admin_tenant))
    actions = {r.action.value for r in page.entries}
    assert "mcp_catalog:create" in actions
    assert "mcp_catalog:delete" in actions
    # No secret value ever lands in audit details.
    for r in page.entries:
        assert "ghp_" not in str(r.details)
