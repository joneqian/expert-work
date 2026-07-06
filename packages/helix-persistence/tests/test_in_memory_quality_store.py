"""Unit tests for the in-memory quality stores — Stream RT-5 (RT-ADR-22/24)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from helix_agent.persistence import (
    InMemoryQualityCandidateSource,
    InMemoryQualityScoreStore,
    QualityCandidate,
)
from helix_agent.protocol import QualityScoreRecord


def _record(*, tenant: UUID, run: UUID, agent: str = "a", overall: int = 4) -> QualityScoreRecord:
    return QualityScoreRecord(
        tenant_id=tenant,
        agent_name=agent,
        agent_version="1",
        run_id=run,
        thread_id=uuid4(),
        overall=overall,
        dimensions={"addressed_request": overall, "coherence": overall, "safety": 5},
        rationale="ok",
        judge_model="m",
    )


@pytest.mark.asyncio
async def test_insert_is_idempotent_per_run() -> None:
    store = InMemoryQualityScoreStore()
    tenant, run = uuid4(), uuid4()
    first = await store.insert(_record(tenant=tenant, run=run, overall=4))
    assert first.id is not None
    assert first.observed_at is not None
    # Re-insert of the same run returns the stored row, does not duplicate.
    again = await store.insert(_record(tenant=tenant, run=run, overall=1))
    assert again.id == first.id
    assert again.overall == 4  # original verdict wins
    rows = await store.list_scores(tenant_id=tenant)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_exists_tracks_scored_runs() -> None:
    store = InMemoryQualityScoreStore()
    tenant, run = uuid4(), uuid4()
    assert await store.exists(tenant_id=tenant, run_id=run) is False
    await store.insert(_record(tenant=tenant, run=run))
    assert await store.exists(tenant_id=tenant, run_id=run) is True
    # Different run / tenant is unaffected.
    assert await store.exists(tenant_id=tenant, run_id=uuid4()) is False
    assert await store.exists(tenant_id=uuid4(), run_id=run) is False


@pytest.mark.asyncio
async def test_count_since_and_list_filters() -> None:
    store = InMemoryQualityScoreStore()
    tenant, other = uuid4(), uuid4()
    await store.insert(_record(tenant=tenant, run=uuid4(), agent="a"))
    await store.insert(_record(tenant=tenant, run=uuid4(), agent="b"))
    await store.insert(_record(tenant=other, run=uuid4(), agent="a"))

    day_start = datetime.now(tz=UTC) - timedelta(hours=1)
    assert await store.count_since(tenant_id=tenant, since=day_start) == 2
    assert await store.count_since(tenant_id=other, since=day_start) == 1
    # Future watermark → nothing counted.
    future = datetime.now(tz=UTC) + timedelta(hours=1)
    assert await store.count_since(tenant_id=tenant, since=future) == 0

    only_a = await store.list_scores(tenant_id=tenant, agent_name="a")
    assert len(only_a) == 1
    assert only_a[0].agent_name == "a"


@pytest.mark.asyncio
async def test_candidate_source_filters_by_watermark_and_orders() -> None:
    base = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    tenant = uuid4()
    cands = [
        QualityCandidate(
            run_id=uuid4(),
            tenant_id=tenant,
            thread_id=uuid4(),
            agent_name="a",
            agent_version="1",
            updated_at=base + timedelta(minutes=m),
        )
        for m in (5, 1, 3)
    ]
    source = InMemoryQualityCandidateSource(cands)
    got = await source.list_candidates(since=base + timedelta(minutes=1), limit=10)
    # Strictly after the watermark, oldest first.
    assert [c.updated_at for c in got] == [base + timedelta(minutes=3), base + timedelta(minutes=5)]
    assert await source.list_candidates(since=base + timedelta(minutes=10), limit=10) == []
