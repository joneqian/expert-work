"""End-to-end tests for the P1-S2.1d ``/v1/eval-runs`` API.

Enqueue + read over the eval-run store, authenticated + tenant-scoped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence import InMemoryEvalRunStore
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import EvalCaseResultRecord, EvalRunStatus
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = DEFAULT_DEV_TENANT_ID


class _Ctx:
    def __init__(self, client: AsyncClient, store: InMemoryEvalRunStore) -> None:
        self.client = client
        self.store = store


@pytest.fixture
async def ctx() -> AsyncIterator[_Ctx]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    store = InMemoryEvalRunStore()
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,
        eval_run_repo=store,
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        yield _Ctx(client, store)


@pytest.mark.asyncio
async def test_enqueue_creates_queued_run(ctx: _Ctx) -> None:
    resp = await ctx.client.post("/v1/eval-runs", json={"suite": "m0_baseline"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["suite"] == "m0_baseline"
    assert body["status"] == "queued"
    assert body["triggered_by"] == "manual"
    # The row landed in the store under the caller's tenant.
    stored = await ctx.store.get_run(run_id=UUID(body["id"]), tenant_id=_TENANT)
    assert stored is not None and stored.status is EvalRunStatus.QUEUED


@pytest.mark.asyncio
async def test_enqueue_unknown_suite_is_422(ctx: _Ctx) -> None:
    resp = await ctx.client.post("/v1/eval-runs", json={"suite": "bogus"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_run_reflects_status_and_summary(ctx: _Ctx) -> None:
    run_id = (await ctx.client.post("/v1/eval-runs", json={"suite": "m0_baseline"})).json()["id"]
    rid = UUID(run_id)
    await ctx.store.set_status(
        run_id=rid,
        tenant_id=_TENANT,
        status=EvalRunStatus.PASSED,
        summary={"pass_count": 15, "total": 15},
    )

    resp = await ctx.client.get(f"/v1/eval-runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "passed"
    assert body["summary"] == {"pass_count": 15, "total": 15}
    assert body["finished_at"] is not None


@pytest.mark.asyncio
async def test_get_unknown_run_is_404(ctx: _Ctx) -> None:
    resp = await ctx.client.get(f"/v1/eval-runs/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_cases(ctx: _Ctx) -> None:
    run_id = (await ctx.client.post("/v1/eval-runs", json={"suite": "m0_baseline"})).json()["id"]
    rid = UUID(run_id)
    await ctx.store.append_case_result(
        EvalCaseResultRecord(
            run_id=rid,
            tenant_id=_TENANT,
            capability="J.1_plan_execute",
            case_id="J.1_plan_execute",
            passed=True,
            scores={"pass_rate": 1.0},
        )
    )

    resp = await ctx.client.get(f"/v1/eval-runs/{run_id}/cases")
    assert resp.status_code == 200
    cases = resp.json()["cases"]
    assert len(cases) == 1
    assert cases[0]["capability"] == "J.1_plan_execute"
    assert cases[0]["passed"] is True
    assert cases[0]["scores"] == {"pass_rate": 1.0}
