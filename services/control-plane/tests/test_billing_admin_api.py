"""API tests for /v1/admin/billing/chargeback — Stream Z (Z-2).

system_admin cross-tenant chargeback: full base/markup/billed/margin split.
Non-admin → 403; cross-tenant aggregation under bypass; optional tenant filter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from helix_agent.common.lifecycle import Lifecycle
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.persistence.billing.ledger import InMemoryTenantBillingLedgerStore
from helix_agent.protocol import Role, TenantBillingLedgerRecord
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_MONTH = date.today().replace(day=1)


def _settings() -> Settings:
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


def _row(*, tenant_id: UUID, base: int, markup: int) -> TenantBillingLedgerRecord:
    now = datetime.now(tz=UTC)
    return TenantBillingLedgerRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        month=_MONTH,
        provider="anthropic",
        model="claude-opus-4-8",
        agent_name="a1",
        input_tokens=100,
        output_tokens=50,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        base_cost_micros=base,
        markup_cost_micros=markup,
        billed_cost_micros=base + markup,
        priced=True,
        rate_card_priced_at=now,
        created_at=now,
        updated_at=now,
    )


class _Ctx:
    def __init__(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        tenant_headers: dict[str, str],
        ledger: InMemoryTenantBillingLedgerStore,
    ) -> None:
        self.client = client
        self.admin_headers = admin_headers
        self.tenant_headers = tenant_headers
        self.ledger = ledger


@pytest.fixture
async def ctx() -> AsyncIterator[_Ctx]:
    lifecycle = Lifecycle()
    lifecycle.mark_ready()
    ledger = InMemoryTenantBillingLedgerStore()
    app = create_app(
        settings=_settings(),
        lifecycle=lifecycle,
        jwt_verifier=build_test_jwt_verifier(),
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
    )
    app.state.tenant_billing_ledger_store = ledger
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
    tenant_jwt = make_test_jwt(tenant_id=uuid4(), subject=str(uuid4()), roles=("admin",))
    tenant_headers = {"Authorization": f"Bearer {tenant_jwt}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://cp.test") as client:
        yield _Ctx(client, admin_headers, tenant_headers, ledger)


@pytest.mark.asyncio
async def test_chargeback_aggregates_cross_tenant_with_full_split(ctx: _Ctx) -> None:
    t1, t2 = uuid4(), uuid4()
    await ctx.ledger.upsert(_row(tenant_id=t1, base=100, markup=20))
    await ctx.ledger.upsert(_row(tenant_id=t2, base=300, markup=60))

    resp = await ctx.client.get("/v1/admin/billing/chargeback", headers=ctx.admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total_base_cost_micros"] == 400
    assert data["total_billed_cost_micros"] == 480
    assert data["total_margin_micros"] == 80
    by_tenant = {t["tenant_id"]: t for t in data["tenants"]}
    assert by_tenant[str(t1)]["base_cost_micros"] == 100
    assert by_tenant[str(t1)]["markup_cost_micros"] == 20
    assert by_tenant[str(t1)]["billed_cost_micros"] == 120
    assert by_tenant[str(t1)]["margin_micros"] == 20
    assert by_tenant[str(t2)]["billed_cost_micros"] == 360


@pytest.mark.asyncio
async def test_chargeback_tenant_filter(ctx: _Ctx) -> None:
    t1, t2 = uuid4(), uuid4()
    await ctx.ledger.upsert(_row(tenant_id=t1, base=100, markup=20))
    await ctx.ledger.upsert(_row(tenant_id=t2, base=300, markup=60))
    resp = await ctx.client.get(
        f"/v1/admin/billing/chargeback?tenant_id={t1}", headers=ctx.admin_headers
    )
    data = resp.json()["data"]
    assert {t["tenant_id"] for t in data["tenants"]} == {str(t1)}
    assert data["total_billed_cost_micros"] == 120


@pytest.mark.asyncio
async def test_chargeback_forbidden_for_non_system_admin(ctx: _Ctx) -> None:
    resp = await ctx.client.get("/v1/admin/billing/chargeback", headers=ctx.tenant_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_chargeback_invalid_month_422(ctx: _Ctx) -> None:
    resp = await ctx.client.get(
        "/v1/admin/billing/chargeback?month=2026-13", headers=ctx.admin_headers
    )
    assert resp.status_code == 422
