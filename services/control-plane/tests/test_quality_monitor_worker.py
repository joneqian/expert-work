"""Unit tests for :class:`QualityMonitorWorker` — Stream RT-5 (RT-ADR-22).

Drives ``run_once`` over an in-memory candidate feed + score store, a stubbed
checkpointer (same shape as the transcript-mirror test), and a fake judge, so
no real LLM / vault / Postgres is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from control_plane.quality_judge import QualityJudgeResult
from control_plane.quality_monitor_worker import QualityMonitorWorker, _is_sampled
from helix_agent.persistence import (
    InMemoryQualityCandidateSource,
    InMemoryQualityScoreStore,
    QualityCandidate,
)
from helix_agent.protocol import QualityScoreRecord

_BASE = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def _msg(mtype: str, content: Any) -> SimpleNamespace:
    return SimpleNamespace(type=mtype, content=content)


class _FakeCheckpointer:
    def __init__(self, by_thread: dict[str, list[SimpleNamespace]]) -> None:
        self._by_thread = by_thread

    async def aget_tuple(self, config: dict[str, Any]) -> SimpleNamespace | None:
        messages = self._by_thread.get(config["configurable"]["thread_id"])
        if messages is None:
            return None
        return SimpleNamespace(checkpoint={"channel_values": {"messages": messages}})


class _FakeJudge:
    def __init__(self, result: QualityJudgeResult | None) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def score(self, *, tenant_id: UUID, prompt: str, reply: str) -> QualityJudgeResult | None:
        self.calls.append((prompt, reply))
        return self._result


class _FakeUsageStore:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def insert(self, record: Any) -> Any:
        self.records.append(record)
        return record


def _verdict() -> QualityJudgeResult:
    return QualityJudgeResult(
        overall=4,
        dimensions={"addressed_request": 5, "coherence": 4, "safety": 5},
        rationale="ok",
        model="claude-haiku-4-5-20251001",
        input_tokens=100,
        output_tokens=12,
    )


def _candidate(tenant: UUID, minute: int, *, thread: UUID | None = None) -> QualityCandidate:
    return QualityCandidate(
        run_id=uuid4(),
        tenant_id=tenant,
        thread_id=thread or uuid4(),
        agent_name="support-bot",
        agent_version="1",
        updated_at=_BASE + timedelta(minutes=minute),
    )


def _worker(
    *,
    candidates: list[QualityCandidate],
    checkpointer: object | None,
    judge: _FakeJudge,
    store: InMemoryQualityScoreStore,
    usage: _FakeUsageStore | None = None,
    rate: int = 100,
    daily_cap: int = 500,
    batch_size: int = 200,
) -> QualityMonitorWorker:
    worker = QualityMonitorWorker(
        candidate_source=InMemoryQualityCandidateSource(candidates),
        score_store=store,
        judge=judge,  # type: ignore[arg-type]
        runtime=SimpleNamespace(durable_checkpointer=checkpointer),  # type: ignore[arg-type]
        usage_store=usage,  # type: ignore[arg-type]
        sampling_rate_pct=rate,
        daily_cap=daily_cap,
        batch_size=batch_size,
    )
    # Watermark just before the batch so all candidates are fresh.
    worker._cursor = _BASE
    return worker


def test_is_sampled_is_deterministic_and_bounded() -> None:
    run = str(uuid4())
    assert _is_sampled(run, 0) is False
    assert _is_sampled(run, 100) is True
    # Same key + rate is stable across calls.
    assert _is_sampled(run, 37) == _is_sampled(run, 37)


@pytest.mark.asyncio
async def test_run_once_samples_judges_persists_and_meters() -> None:
    tenant = uuid4()
    th1, th2 = uuid4(), uuid4()
    cands = [_candidate(tenant, 1, thread=th1), _candidate(tenant, 2, thread=th2)]
    cp = _FakeCheckpointer(
        {
            str(th1): [_msg("human", "charged twice"), _msg("ai", "refund opened")],
            str(th2): [_msg("human", "reset password"), _msg("ai", "link sent")],
        }
    )
    judge, store, usage = _FakeJudge(_verdict()), InMemoryQualityScoreStore(), _FakeUsageStore()
    worker = _worker(candidates=cands, checkpointer=cp, judge=judge, store=store, usage=usage)

    scored = await worker.run_once()
    assert scored == 2
    rows = await store.list_scores(tenant_id=tenant)
    assert len(rows) == 2
    assert {r.overall for r in rows} == {4}
    # The judge saw each thread's latest exchange.
    assert ("charged twice", "refund opened") in judge.calls
    # Aux chargeback recorded per judged run.
    assert len(usage.records) == 2
    assert usage.records[0].usage_kind == "quality_sampling"
    # Watermark advanced to the newest candidate.
    assert worker._cursor == _BASE + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_run_once_respects_daily_cap() -> None:
    tenant = uuid4()
    th1, th2 = uuid4(), uuid4()
    cands = [_candidate(tenant, 1, thread=th1), _candidate(tenant, 2, thread=th2)]
    cp = _FakeCheckpointer(
        {
            str(th1): [_msg("human", "q1"), _msg("ai", "a1")],
            str(th2): [_msg("human", "q2"), _msg("ai", "a2")],
        }
    )
    judge, store = _FakeJudge(_verdict()), InMemoryQualityScoreStore()
    worker = _worker(candidates=cands, checkpointer=cp, judge=judge, store=store, daily_cap=1)

    scored = await worker.run_once()
    assert scored == 1  # second candidate hits the per-tenant daily cap
    assert len(await store.list_scores(tenant_id=tenant)) == 1


@pytest.mark.asyncio
async def test_run_once_skips_when_no_assistant_reply() -> None:
    tenant = uuid4()
    th = uuid4()
    cands = [_candidate(tenant, 1, thread=th)]
    cp = _FakeCheckpointer({str(th): [_msg("human", "only a question")]})
    judge, store = _FakeJudge(_verdict()), InMemoryQualityScoreStore()
    worker = _worker(candidates=cands, checkpointer=cp, judge=judge, store=store)

    assert await worker.run_once() == 0
    assert judge.calls == []  # never reached the judge


@pytest.mark.asyncio
async def test_run_once_drops_run_when_judge_returns_none() -> None:
    tenant = uuid4()
    th = uuid4()
    cands = [_candidate(tenant, 1, thread=th)]
    cp = _FakeCheckpointer({str(th): [_msg("human", "q"), _msg("ai", "a")]})
    judge, store = _FakeJudge(None), InMemoryQualityScoreStore()
    worker = _worker(candidates=cands, checkpointer=cp, judge=judge, store=store)

    assert await worker.run_once() == 0
    assert await store.list_scores(tenant_id=tenant) == []


@pytest.mark.asyncio
async def test_run_once_ignores_unsampled_runs() -> None:
    tenant = uuid4()
    th = uuid4()
    cands = [_candidate(tenant, 1, thread=th)]
    cp = _FakeCheckpointer({str(th): [_msg("human", "q"), _msg("ai", "a")]})
    judge, store = _FakeJudge(_verdict()), InMemoryQualityScoreStore()
    worker = _worker(candidates=cands, checkpointer=cp, judge=judge, store=store, rate=0)

    assert await worker.run_once() == 0
    assert judge.calls == []
    # Cursor still advances past the scanned (but unsampled) candidate.
    assert worker._cursor == _BASE + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_run_once_drains_backlog_across_batches() -> None:
    # 3 candidates with batch_size=2 → two batches drained in one cycle.
    tenant = uuid4()
    threads = [uuid4(), uuid4(), uuid4()]
    cands = [_candidate(tenant, m, thread=t) for m, t in zip((1, 2, 3), threads, strict=True)]
    cp = _FakeCheckpointer(
        {str(t): [_msg("human", f"q{i}"), _msg("ai", f"a{i}")] for i, t in enumerate(threads)}
    )
    judge, store = _FakeJudge(_verdict()), InMemoryQualityScoreStore()
    worker = _worker(candidates=cands, checkpointer=cp, judge=judge, store=store, batch_size=2)

    scored = await worker.run_once()
    assert scored == 3  # all drained despite batch_size < backlog
    assert len(await store.list_scores(tenant_id=tenant)) == 3
    assert worker._cursor == _BASE + timedelta(minutes=3)


@pytest.mark.asyncio
async def test_run_once_skips_already_scored_run_without_re_judging() -> None:
    tenant = uuid4()
    th = uuid4()
    cand = _candidate(tenant, 1, thread=th)
    cp = _FakeCheckpointer({str(th): [_msg("human", "q"), _msg("ai", "a")]})
    judge, store, usage = _FakeJudge(_verdict()), InMemoryQualityScoreStore(), _FakeUsageStore()
    # Pre-seed a verdict for this run — a re-scan must not re-judge / re-charge.
    await store.insert(
        QualityScoreRecord(
            tenant_id=tenant,
            agent_name="support-bot",
            agent_version="1",
            run_id=cand.run_id,
            thread_id=th,
            overall=3,
            dimensions={},
            rationale="prior",
            judge_model="m",
        )
    )
    worker = _worker(candidates=[cand], checkpointer=cp, judge=judge, store=store, usage=usage)

    assert await worker.run_once() == 0
    assert judge.calls == []  # never re-judged
    assert usage.records == []  # never re-charged
    assert len(await store.list_scores(tenant_id=tenant)) == 1


@pytest.mark.asyncio
async def test_run_once_short_circuits_without_checkpointer() -> None:
    tenant = uuid4()
    cands = [_candidate(tenant, 1)]
    judge, store = _FakeJudge(_verdict()), InMemoryQualityScoreStore()
    worker = _worker(candidates=cands, checkpointer=None, judge=judge, store=store)
    assert await worker.run_once() == 0
