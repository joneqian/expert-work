"""Tests for GET /v1/sandbox-egress-audit (sandbox-egress §3.1 Phase 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.persistence.sandbox_egress_audit import EgressAuditRecord
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = DEFAULT_DEV_TENANT_ID
_TENANT_OTHER = UUID("99999999-9999-9999-9999-999999999999")


def _rec(row_id: int, *, tenant_id: UUID = _TENANT, verdict: str = "allowed") -> EgressAuditRecord:
    return EgressAuditRecord(
        id=row_id,
        tenant_id=tenant_id,
        agent_name="pptx-agent",
        agent_version="1.0.0",
        sandbox_id=f"sbx-{row_id}",
        target_host="api.openai.com",
        target_port=443,
        verdict=verdict,
        bytes_up=100,
        bytes_down=200,
        duration_ms=12,
        error_msg=None,
        occurred_at=datetime.now(UTC),
    )


class _Ctx:
    def __init__(self, client: AsyncClient, app: object) -> None:
        self.client = client
        self.app = app

    def seed(self, *records: EgressAuditRecord) -> None:
        self.app.state.sandbox_egress_audit_store.records.extend(records)  # type: ignore[attr-defined]


def _settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


@pytest.fixture
async def ctx() -> AsyncIterator[_Ctx]:
    app = create_app(
        settings=_settings(),
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,
        enable_curation_worker=False,
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        yield _Ctx(client, app)


async def test_lists_seeded_rows_for_home_tenant(ctx: _Ctx) -> None:
    ctx.seed(_rec(1), _rec(2))
    resp = await ctx.client.get("/v1/sandbox-egress-audit")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["id"] for e in body["items"]] == [2, 1]  # newest-first
    assert body["applied_scope"] == str(_TENANT)
    assert body["items"][0]["target_host"] == "api.openai.com"
    assert "bytes_up" in body["items"][0]


async def test_filters_by_verdict(ctx: _Ctx) -> None:
    ctx.seed(_rec(1, verdict="allowed"), _rec(2, verdict="blocked_allowlist"))
    resp = await ctx.client.get("/v1/sandbox-egress-audit", params={"verdict": "blocked_allowlist"})
    assert resp.status_code == 200
    assert {e["verdict"] for e in resp.json()["items"]} == {"blocked_allowlist"}


async def test_other_tenant_rows_not_visible_in_home_scope(ctx: _Ctx) -> None:
    ctx.seed(_rec(1, tenant_id=_TENANT), _rec(2, tenant_id=_TENANT_OTHER))
    resp = await ctx.client.get("/v1/sandbox-egress-audit")
    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()["items"]] == [1]


async def test_invalid_verdict_rejected(ctx: _Ctx) -> None:
    resp = await ctx.client.get("/v1/sandbox-egress-audit", params={"verdict": "nonsense"})
    assert resp.status_code == 422  # not in the EgressVerdict Literal


async def test_limit_over_max_rejected(ctx: _Ctx) -> None:
    resp = await ctx.client.get("/v1/sandbox-egress-audit", params={"limit": 999})
    assert resp.status_code == 422
