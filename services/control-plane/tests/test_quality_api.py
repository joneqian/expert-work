"""End-to-end tests for the RT-5 ``/v1/quality`` dashboard read API.

Seeds the in-memory quality-score / drift-alert stores that ``create_app``
attaches to ``app.state`` (no repo-injection kwarg needed), then reads them
back through the authenticated, tenant-scoped router.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import QualityDriftAlertRecord, QualityScoreRecord
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = DEFAULT_DEV_TENANT_ID


class _Ctx:
    def __init__(self, client: AsyncClient, app: object) -> None:
        self.client = client
        self.scores = app.state.quality_score_store  # type: ignore[attr-defined]
        self.alerts = app.state.quality_drift_alert_store  # type: ignore[attr-defined]


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
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=stub_agent_runtime(),
        enable_scheduler=False,
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        yield _Ctx(client, app)


def _score(
    *, agent: str, overall: int, at: datetime, tenant: object = _TENANT
) -> QualityScoreRecord:
    return QualityScoreRecord(
        tenant_id=tenant,  # type: ignore[arg-type]
        agent_name=agent,
        agent_version="1",
        run_id=uuid4(),
        thread_id=uuid4(),
        overall=overall,
        dimensions={"addressed_request": overall, "coherence": overall, "safety": 5},
        rationale="ok",
        judge_model="claude-haiku-4-5-20251001",
        observed_at=at,
    )


@pytest.mark.asyncio
async def test_list_scores_newest_first_carries_drill_fields(ctx: _Ctx) -> None:
    now = datetime.now(tz=UTC)
    older = await ctx.scores.insert(_score(agent="a", overall=5, at=now - timedelta(hours=2)))
    newer = await ctx.scores.insert(_score(agent="a", overall=2, at=now - timedelta(minutes=5)))

    resp = await ctx.client.get("/v1/quality/scores")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [it["overall"] for it in items] == [2, 5]  # newest first
    top = items[0]
    # Drill fields present so the UI can link to run_detail.
    assert top["run_id"] == str(newer.run_id)
    assert top["thread_id"] == str(newer.thread_id)
    assert top["dimensions"]["addressed_request"] == 2
    assert top["rationale"] == "ok"
    assert items[1]["run_id"] == str(older.run_id)


@pytest.mark.asyncio
async def test_list_scores_filters_by_agent(ctx: _Ctx) -> None:
    now = datetime.now(tz=UTC)
    await ctx.scores.insert(_score(agent="a", overall=4, at=now))
    await ctx.scores.insert(_score(agent="b", overall=3, at=now))

    resp = await ctx.client.get("/v1/quality/scores", params={"agent_name": "b"})
    items = resp.json()["items"]
    assert [it["agent_name"] for it in items] == ["b"]


@pytest.mark.asyncio
async def test_list_scores_window_excludes_stale(ctx: _Ctx) -> None:
    now = datetime.now(tz=UTC)
    await ctx.scores.insert(_score(agent="a", overall=4, at=now - timedelta(hours=1)))
    await ctx.scores.insert(_score(agent="a", overall=4, at=now - timedelta(hours=200)))

    resp = await ctx.client.get("/v1/quality/scores", params={"window_h": 168})
    assert len(resp.json()["items"]) == 1


@pytest.mark.asyncio
async def test_list_scores_tenant_scoped(ctx: _Ctx) -> None:
    now = datetime.now(tz=UTC)
    await ctx.scores.insert(_score(agent="a", overall=4, at=now, tenant=uuid4()))

    resp = await ctx.client.get("/v1/quality/scores")
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_list_drift_alerts_newest_first(ctx: _Ctx) -> None:
    now = datetime.now(tz=UTC)
    await ctx.alerts.insert(
        QualityDriftAlertRecord(
            tenant_id=_TENANT,
            agent_name="a",
            recent_mean=3.0,
            baseline_mean=5.0,
            drift_pct=0.4,
            recent_count=10,
            baseline_count=40,
            detected_at=now - timedelta(hours=3),
        )
    )
    await ctx.alerts.insert(
        QualityDriftAlertRecord(
            tenant_id=_TENANT,
            agent_name="b",
            recent_mean=2.0,
            baseline_mean=4.0,
            drift_pct=0.5,
            recent_count=12,
            baseline_count=50,
            detected_at=now - timedelta(minutes=10),
        )
    )

    resp = await ctx.client.get("/v1/quality/drift-alerts")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [it["agent_name"] for it in items] == ["b", "a"]
    assert items[0]["drift_pct"] == 0.5
    assert items[0]["recent_mean"] == 2.0


@pytest.mark.asyncio
async def test_list_drift_alerts_tenant_scoped(ctx: _Ctx) -> None:
    await ctx.alerts.insert(
        QualityDriftAlertRecord(
            tenant_id=uuid4(),
            agent_name="a",
            recent_mean=3.0,
            baseline_mean=5.0,
            drift_pct=0.4,
            recent_count=10,
            baseline_count=40,
        )
    )
    resp = await ctx.client.get("/v1/quality/drift-alerts")
    assert resp.json()["items"] == []
