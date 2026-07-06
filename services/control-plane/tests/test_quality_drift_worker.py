"""Unit tests for :class:`QualityDriftWorker` — Stream RT-5 (RT-ADR-24/25).

Drives ``run_once`` over in-memory score + alert stores and fake webhook
endpoint / delivery stores, so no real Postgres / network is touched. Scores
are seeded with explicit ``observed_at`` to land in the recent / baseline
windows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from control_plane.quality_drift_worker import QualityDriftWorker
from helix_agent.persistence import (
    InMemoryQualityDriftAlertStore,
    InMemoryQualityScoreStore,
)
from helix_agent.protocol import QualityScoreRecord

_HOUR = 3600.0


@dataclass
class _Ep:
    id: UUID
    tenant_id: UUID
    event_types: tuple[str, ...]
    agent_name: str | None


class _FakeEndpointStore:
    def __init__(self, endpoints: list[_Ep]) -> None:
        self._endpoints = endpoints

    async def list_enabled_all_tenants(self) -> list[_Ep]:
        return self._endpoints


class _FakeDeliveryStore:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def exists_for_event(self, *, endpoint_id: UUID, event_id: str) -> bool:
        return any(d.endpoint_id == endpoint_id and d.event_id == event_id for d in self.created)

    async def create(self, record: Any) -> Any:
        self.created.append(record)
        return record


@dataclass
class _Fixture:
    worker: QualityDriftWorker
    scores: InMemoryQualityScoreStore
    alerts: InMemoryQualityDriftAlertStore
    deliveries: _FakeDeliveryStore
    tenant: UUID
    endpoints: list[_Ep] = field(default_factory=list)


def _score(tenant: UUID, agent: str, overall: int, at: datetime) -> QualityScoreRecord:
    return QualityScoreRecord(
        tenant_id=tenant,
        agent_name=agent,
        agent_version="1",
        run_id=uuid4(),
        thread_id=uuid4(),
        overall=overall,
        dimensions={},
        rationale="",
        judge_model="m",
        observed_at=at,
    )


async def _seed(
    scores: InMemoryQualityScoreStore,
    tenant: UUID,
    agent: str,
    *,
    recent: list[int],
    baseline: list[int],
    now: datetime,
) -> None:
    # Recent window (< 1h ago) and baseline window (1-2h ago).
    for i, overall in enumerate(recent):
        await scores.insert(_score(tenant, agent, overall, now - timedelta(minutes=5 + i * 5)))
    for i, overall in enumerate(baseline):
        await scores.insert(_score(tenant, agent, overall, now - timedelta(minutes=70 + i * 5)))


def _build(
    scores: InMemoryQualityScoreStore,
    *,
    tenant: UUID,
    endpoints: list[_Ep] | None = None,
    min_samples: int = 3,
    threshold: float = 0.15,
    cooldown_s: float = 24 * _HOUR,
) -> _Fixture:
    alerts = InMemoryQualityDriftAlertStore()
    deliveries = _FakeDeliveryStore()
    eps = endpoints or []
    worker = QualityDriftWorker(
        score_store=scores,
        alert_store=alerts,
        endpoint_store=_FakeEndpointStore(eps),  # type: ignore[arg-type]
        delivery_store=deliveries,  # type: ignore[arg-type]
        recent_window_s=_HOUR,
        baseline_window_s=_HOUR,
        min_samples=min_samples,
        drift_threshold=threshold,
        cooldown_s=cooldown_s,
    )
    return _Fixture(worker, scores, alerts, deliveries, tenant, eps)


@pytest.mark.asyncio
async def test_drift_triggers_alert_and_emits_webhook() -> None:
    tenant, agent = uuid4(), "support-bot"
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    await _seed(scores, tenant, agent, recent=[3, 3, 3, 3], baseline=[5, 5, 5, 5], now=now)
    ep = _Ep(id=uuid4(), tenant_id=tenant, event_types=("quality.drift",), agent_name=None)
    fx = _build(scores, tenant=tenant, endpoints=[ep])

    raised = await fx.worker.run_once()
    assert raised == 1
    stored = await fx.alerts.list_alerts(tenant_id=tenant)
    assert len(stored) == 1
    assert stored[0].recent_mean == 3.0
    assert stored[0].baseline_mean == 5.0
    assert stored[0].drift_pct == pytest.approx(0.4)
    # Off-spine webhook enqueued: run_id=None, quality.drift, payload carries agent.
    assert len(fx.deliveries.created) == 1
    delivery = fx.deliveries.created[0]
    assert delivery.event_type == "quality.drift"
    assert delivery.run_id is None
    assert delivery.payload["agent_name"] == agent
    assert delivery.event_id.startswith("quality_drift:")


@pytest.mark.asyncio
async def test_multi_tenant_cycle_scopes_webhooks_per_tenant() -> None:
    t1, t2 = uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    await _seed(scores, t1, "a", recent=[3, 3, 3, 3], baseline=[5, 5, 5, 5], now=now)
    await _seed(scores, t2, "b", recent=[2, 2, 2, 2], baseline=[5, 5, 5, 5], now=now)
    ep1 = _Ep(id=uuid4(), tenant_id=t1, event_types=("quality.drift",), agent_name=None)
    ep2 = _Ep(id=uuid4(), tenant_id=t2, event_types=("quality.drift",), agent_name=None)
    fx = _build(scores, tenant=t1, endpoints=[ep1, ep2])

    raised = await fx.worker.run_once()
    assert raised == 2
    # Each tenant's alert fans out only to that tenant's endpoint.
    by_endpoint = {d.endpoint_id: d for d in fx.deliveries.created}
    assert set(by_endpoint) == {ep1.id, ep2.id}
    assert by_endpoint[ep1.id].tenant_id == t1
    assert by_endpoint[ep2.id].tenant_id == t2


@pytest.mark.asyncio
async def test_one_agents_emit_failure_does_not_starve_the_cycle() -> None:
    class _FailOnceDeliveryStore(_FakeDeliveryStore):
        def __init__(self) -> None:
            super().__init__()
            self._failed = False

        async def create(self, record: Any) -> Any:
            if not self._failed:
                self._failed = True
                msg = "transient db error"
                raise RuntimeError(msg)
            return await super().create(record)

    t1, t2 = uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    await _seed(scores, t1, "a", recent=[3, 3, 3, 3], baseline=[5, 5, 5, 5], now=now)
    await _seed(scores, t2, "b", recent=[3, 3, 3, 3], baseline=[5, 5, 5, 5], now=now)
    ep1 = _Ep(id=uuid4(), tenant_id=t1, event_types=("quality.drift",), agent_name=None)
    ep2 = _Ep(id=uuid4(), tenant_id=t2, event_types=("quality.drift",), agent_name=None)
    alerts = InMemoryQualityDriftAlertStore()
    deliveries = _FailOnceDeliveryStore()
    worker = QualityDriftWorker(
        score_store=scores,
        alert_store=alerts,
        endpoint_store=_FakeEndpointStore([ep1, ep2]),  # type: ignore[arg-type]
        delivery_store=deliveries,  # type: ignore[arg-type]
        recent_window_s=_HOUR,
        baseline_window_s=_HOUR,
        min_samples=3,
        drift_threshold=0.15,
        cooldown_s=24 * _HOUR,
    )

    # First agent's emit raises; the second is still processed (isolated).
    await worker.run_once()
    # Both alerts persisted; only the surviving agent's webhook enqueued.
    all_alerts = await alerts.list_alerts(tenant_id=t1)
    all_alerts += await alerts.list_alerts(tenant_id=t2)
    assert len(all_alerts) == 2
    assert len(deliveries.created) == 1


@pytest.mark.asyncio
async def test_cooldown_suppresses_repeat_alert() -> None:
    tenant, agent = uuid4(), "a"
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    await _seed(scores, tenant, agent, recent=[3, 3, 3, 3], baseline=[5, 5, 5, 5], now=now)
    fx = _build(scores, tenant=tenant)

    assert await fx.worker.run_once() == 1
    # Second cycle within the cooldown window: no new alert.
    assert await fx.worker.run_once() == 0
    assert len(await fx.alerts.list_alerts(tenant_id=tenant)) == 1


@pytest.mark.asyncio
async def test_no_alert_when_recent_samples_below_min() -> None:
    tenant, agent = uuid4(), "a"
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    await _seed(scores, tenant, agent, recent=[3, 3], baseline=[5, 5, 5, 5], now=now)
    fx = _build(scores, tenant=tenant, min_samples=3)
    assert await fx.worker.run_once() == 0


@pytest.mark.asyncio
async def test_no_alert_without_baseline() -> None:
    tenant, agent = uuid4(), "a"
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    await _seed(scores, tenant, agent, recent=[3, 3, 3, 3], baseline=[], now=now)
    fx = _build(scores, tenant=tenant)
    assert await fx.worker.run_once() == 0


@pytest.mark.asyncio
async def test_no_alert_on_improvement() -> None:
    tenant, agent = uuid4(), "a"
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    # Recent HIGHER than baseline → negative drift_pct → no alert.
    await _seed(scores, tenant, agent, recent=[5, 5, 5, 5], baseline=[3, 3, 3, 3], now=now)
    fx = _build(scores, tenant=tenant)
    assert await fx.worker.run_once() == 0


@pytest.mark.asyncio
async def test_no_alert_when_drop_below_threshold() -> None:
    tenant, agent = uuid4(), "a"
    now = datetime.now(tz=UTC)
    scores = InMemoryQualityScoreStore()
    # 4.0 vs 4.2 → ~4.8% drop, below the 15% threshold.
    await _seed(scores, tenant, agent, recent=[4, 4, 4, 4], baseline=[4, 4, 4, 5], now=now)
    fx = _build(scores, tenant=tenant, threshold=0.15)
    assert await fx.worker.run_once() == 0
