"""SE-16 (SE-A45) — the sampled quality screen over implicit candidates.

Exercises ``_ImplicitScreener`` with a deterministic fake aux model: the
per-tenant sample rate gates whether the judge is even called; a judged
candidate survives only on a passing (>=4) conversation-quality score.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.memory_consolidator import ConsolidatorLLMReply
from control_plane.skill_evolution_wiring import _AuxText, _ImplicitScreener
from helix_agent.protocol import CurationCandidateRecord
from helix_agent.runtime.storage import InMemoryObjectStore
from orchestrator.trajectory import TrajectoryReader, TrajectoryRecord, TrajectoryRecorder

_NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


class _FakeAux:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def __call__(
        self, *, prompt: str, model: str | None, tenant_id: UUID
    ) -> ConsolidatorLLMReply:
        self.calls += 1
        return ConsolidatorLLMReply(text=self.text, model="fake")


async def _seeded(tenant: UUID) -> tuple[TrajectoryReader, str, UUID]:
    object_store = InMemoryObjectStore()
    thread = uuid4()
    await TrajectoryRecorder(object_store=object_store).record(
        TrajectoryRecord(
            thread_id=thread,
            tenant_id=tenant,
            outcome="success",
            messages=[HumanMessage(content="do the report"), AIMessage(content="done")],
            finished_at=_NOW,
        )
    )
    reader = TrajectoryReader(object_store=object_store)
    (key,) = await reader.list_keys(tenant_id=tenant, outcome="success")
    return reader, key, thread


def _candidate(*, tenant: UUID, thread: UUID, key: str) -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=tenant,
        agent_name="assistant",
        thread_id=thread,
        trajectory_key=key,
        outcome="success",
        signal="implicit_success",
        detected_at=_NOW,
    )


def _screener(aux: _FakeAux, reader: TrajectoryReader, pct: int) -> _ImplicitScreener:
    async def sample_pct(tenant_id: UUID) -> int:
        return pct

    return _ImplicitScreener(aux=_AuxText(aux), reader=reader, sample_pct=sample_pct)


@pytest.mark.asyncio
async def test_unsampled_candidate_drops_without_a_judge_call() -> None:
    tenant = uuid4()
    reader, key, thread = await _seeded(tenant)
    aux = _FakeAux("5")

    decision = await _screener(aux, reader, pct=0)(_candidate(tenant=tenant, thread=thread, key=key))

    assert decision.proceed is False
    assert decision.reason == "not_sampled"
    assert aux.calls == 0


@pytest.mark.asyncio
async def test_high_score_passes_the_screen() -> None:
    tenant = uuid4()
    reader, key, thread = await _seeded(tenant)
    aux = _FakeAux("5")

    decision = await _screener(aux, reader, pct=100)(
        _candidate(tenant=tenant, thread=thread, key=key)
    )

    assert decision.proceed is True
    assert decision.reason == "judge_passed"
    assert decision.score == 5
    assert aux.calls == 1


@pytest.mark.asyncio
async def test_low_score_is_filtered() -> None:
    tenant = uuid4()
    reader, key, thread = await _seeded(tenant)
    aux = _FakeAux("2")

    decision = await _screener(aux, reader, pct=100)(
        _candidate(tenant=tenant, thread=thread, key=key)
    )

    assert decision.proceed is False
    assert decision.reason == "judge_filtered"
    assert decision.score == 2


@pytest.mark.asyncio
async def test_unparseable_judge_reply_is_filtered() -> None:
    tenant = uuid4()
    reader, key, thread = await _seeded(tenant)
    aux = _FakeAux("the conversation looks fine to me")

    decision = await _screener(aux, reader, pct=100)(
        _candidate(tenant=tenant, thread=thread, key=key)
    )

    assert decision.proceed is False
    assert decision.score == 0


@pytest.mark.asyncio
async def test_missing_trajectory_drops_without_a_judge_call() -> None:
    tenant = uuid4()
    reader, _key, thread = await _seeded(tenant)
    aux = _FakeAux("5")

    decision = await _screener(aux, reader, pct=100)(
        _candidate(tenant=tenant, thread=thread, key="gone/does-not-exist.jsonl")
    )

    assert decision.proceed is False
    assert decision.reason == "trajectory_missing"
    assert aux.calls == 0
