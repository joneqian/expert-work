"""API tests for /v1/platform/rate-card — Stream Y (Y-3).

Covers the system_admin CRUD surface, the platform-scope gating (tenant admin →
403 on every endpoint), duplicate (409 RATE_CARD_DUPLICATE) / not-found (404)
mappings, the Upsert validators (422 on bad provider/model + negative micros),
and list filters.

Fixtures mirror test_mcp_catalog_api.py: system_admin is established by seeding a
SYSTEM_ADMIN role binding; a plain ``admin`` JWT stands in for a tenant admin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from expert_work.common.lifecycle import Lifecycle
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import Role
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


def _valid_rate(
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> dict[str, object]:
    return {
        "provider": provider,
        "model": model,
        "input_per_mtok_micros": 15_000_000,
        "output_per_mtok_micros": 75_000_000,
    }


class _Ctx:
    def __init__(
        self,
        client: AsyncClient,
        app: object,
        admin_headers: dict[str, str],
        tenant_admin_headers: dict[str, str],
    ) -> None:
        self.client = client
        self.app = app
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
    sys_admin_id = uuid4()
    await app.state.role_binding_repo.create(  # type: ignore[attr-defined]
        subject_type="user",
        subject_id=sys_admin_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="seed",
    )
    admin_jwt = make_test_jwt(tenant_id=uuid4(), subject=str(sys_admin_id))
    admin_headers = {"Authorization": f"Bearer {admin_jwt}"}
    tenant_admin_jwt = make_test_jwt(tenant_id=uuid4(), subject=str(uuid4()), roles=("admin",))
    tenant_admin_headers = {"Authorization": f"Bearer {tenant_admin_jwt}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        yield _Ctx(client, app, admin_headers, tenant_admin_headers)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_get_list_patch_delete(ctx: _Ctx) -> None:
    create = await ctx.client.post(
        "/v1/platform/rate-card", json=_valid_rate(), headers=ctx.admin_headers
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["success"] is True
    assert body["data"]["provider"] == "anthropic"
    assert body["data"]["input_per_mtok_micros"] == 15_000_000
    rate_id = body["data"]["id"]

    got = await ctx.client.get(f"/v1/platform/rate-card/{rate_id}", headers=ctx.admin_headers)
    assert got.status_code == 200
    assert got.json()["data"]["input_per_mtok_micros"] == 15_000_000

    lst = await ctx.client.get("/v1/platform/rate-card", headers=ctx.admin_headers)
    assert lst.status_code == 200
    ids = {r["id"] for r in lst.json()["data"]}
    assert rate_id in ids

    patch = await ctx.client.patch(
        f"/v1/platform/rate-card/{rate_id}",
        json={"input_per_mtok_micros": 20_000_000},
        headers=ctx.admin_headers,
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["data"]["input_per_mtok_micros"] == 20_000_000

    delete = await ctx.client.delete(f"/v1/platform/rate-card/{rate_id}", headers=ctx.admin_headers)
    assert delete.status_code == 204
    gone = await ctx.client.get(f"/v1/platform/rate-card/{rate_id}", headers=ctx.admin_headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_list_filters(ctx: _Ctx) -> None:
    await ctx.client.post("/v1/platform/rate-card", json=_valid_rate(), headers=ctx.admin_headers)
    await ctx.client.post(
        "/v1/platform/rate-card",
        json=_valid_rate(provider="openai", model="gpt-5.5"),
        headers=ctx.admin_headers,
    )
    lst = await ctx.client.get(
        "/v1/platform/rate-card", params={"provider": "openai"}, headers=ctx.admin_headers
    )
    assert lst.status_code == 200
    providers = {r["provider"] for r in lst.json()["data"]}
    assert providers == {"openai"}


# ---------------------------------------------------------------------------
# Error mappings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_409(ctx: _Ctx) -> None:
    first = await ctx.client.post(
        "/v1/platform/rate-card", json=_valid_rate(), headers=ctx.admin_headers
    )
    assert first.status_code == 201
    dup = await ctx.client.post(
        "/v1/platform/rate-card", json=_valid_rate(), headers=ctx.admin_headers
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "RATE_CARD_DUPLICATE"


@pytest.mark.asyncio
async def test_get_patch_delete_missing_404(ctx: _Ctx) -> None:
    missing = str(uuid4())
    g = await ctx.client.get(f"/v1/platform/rate-card/{missing}", headers=ctx.admin_headers)
    assert g.status_code == 404
    p = await ctx.client.patch(
        f"/v1/platform/rate-card/{missing}",
        json={"input_per_mtok_micros": 1},
        headers=ctx.admin_headers,
    )
    assert p.status_code == 404
    d = await ctx.client.delete(f"/v1/platform/rate-card/{missing}", headers=ctx.admin_headers)
    assert d.status_code == 404


@pytest.mark.asyncio
async def test_create_unknown_provider_model_422(ctx: _Ctx) -> None:
    bad_provider = await ctx.client.post(
        "/v1/platform/rate-card",
        json=_valid_rate(provider="nope"),
        headers=ctx.admin_headers,
    )
    assert bad_provider.status_code == 422
    bad_model = await ctx.client.post(
        "/v1/platform/rate-card",
        json=_valid_rate(model="not-a-model"),
        headers=ctx.admin_headers,
    )
    assert bad_model.status_code == 422


@pytest.mark.asyncio
async def test_create_negative_micros_422(ctx: _Ctx) -> None:
    bad = _valid_rate()
    bad["input_per_mtok_micros"] = -1
    resp = await ctx.client.post("/v1/platform/rate-card", json=bad, headers=ctx.admin_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Platform gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_admin_forbidden_on_every_endpoint(ctx: _Ctx) -> None:
    h = ctx.tenant_admin_headers
    some_id = str(uuid4())
    created = await ctx.client.post("/v1/platform/rate-card", json=_valid_rate(), headers=h)
    listed = await ctx.client.get("/v1/platform/rate-card", headers=h)
    got = await ctx.client.get(f"/v1/platform/rate-card/{some_id}", headers=h)
    patched = await ctx.client.patch(
        f"/v1/platform/rate-card/{some_id}", json={"input_per_mtok_micros": 1}, headers=h
    )
    deleted = await ctx.client.delete(f"/v1/platform/rate-card/{some_id}", headers=h)
    assert created.status_code == 403
    assert listed.status_code == 403
    assert got.status_code == 403
    assert patched.status_code == 403
    assert deleted.status_code == 403
