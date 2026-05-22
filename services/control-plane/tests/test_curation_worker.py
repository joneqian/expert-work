"""Unit tests for the curation worker — Stream J.12 (Mini-ADR J-43)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.curation_worker import CurationWorker
from helix_agent.persistence import InMemoryCurationCandidateStore, InMemoryThreadMetaStore
from helix_agent.persistence.feedback_store import FeedbackRecord, InMemoryFeedbackStore
from helix_agent.protocol import CandidateStatus, TrajectoryOutcome
from helix_agent.runtime.storage import InMemoryObjectStore
from orchestrator.trajectory import TrajectoryReader, TrajectoryRecord, TrajectoryRecorder

_BASE = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


class _Fixture:
    """A worker wired over in-memory backends, with seed helpers."""

    def __init__(self) -> None:
        self.object_store = InMemoryObjectStore()
        self.candidates = InMemoryCurationCandidateStore()
        self.threads = InMemoryThreadMetaStore()
        self.feedback = InMemoryFeedbackStore()
        self.worker = CurationWorker(
            trajectory_reader=TrajectoryReader(object_store=self.object_store),
            candidate_store=self.candidates,
            thread_store=self.threads,
            feedback_store=self.feedback,
            interval_s=60,
        )

    async def seed_trajectory(
        self,
        *,
        tenant_id: UUID,
        thread_id: UUID,
        outcome: TrajectoryOutcome,
        user_id: UUID | None = None,
    ) -> None:
        await TrajectoryRecorder(object_store=self.object_store).record(
            TrajectoryRecord(
                thread_id=thread_id,
                tenant_id=tenant_id,
                outcome=outcome,
                messages=[HumanMessage(content="hi"), AIMessage(content="bye")],
                user_id=user_id,
                finished_at=_BASE,
            )
        )

    async def seed_thread(
        self,
        *,
        tenant_id: UUID,
        thread_id: UUID,
        agent_name: str | None = "reporter",
        user_id: UUID | None = None,
    ) -> None:
        await self.threads.create(
            thread_id=thread_id,
            tenant_id=tenant_id,
            created_by="user@example.com",
            user_id=user_id,
            agent_name=agent_name,
            agent_version="1.0.0",
        )

    async def seed_feedback(self, *, tenant_id: UUID, thread_id: UUID, rating: str) -> None:
        await self.feedback.insert(
            FeedbackRecord(
                tenant_id=tenant_id, thread_id=thread_id, rating=rating, actor_id="user@example.com"
            )
        )


@pytest.mark.asyncio
async def test_failed_outcome_becomes_candidate() -> None:
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="failed")

    detected = await fx.worker.run_once()
    assert detected == 1
    rows = await fx.candidates.list_for_review(tenant_id=tenant)
    assert len(rows) == 1
    assert rows[0].signal == "failed_outcome"
    assert rows[0].feedback_rating is None
    assert rows[0].status is CandidateStatus.PENDING


@pytest.mark.asyncio
async def test_negative_feedback_becomes_candidate() -> None:
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="success")
    await fx.seed_feedback(tenant_id=tenant, thread_id=thread, rating="down")

    detected = await fx.worker.run_once()
    assert detected == 1
    rows = await fx.candidates.list_for_review(tenant_id=tenant)
    assert rows[0].signal == "negative_feedback"
    assert rows[0].feedback_rating == "down"


@pytest.mark.asyncio
async def test_positive_feedback_becomes_golden_candidate() -> None:
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="success")
    await fx.seed_feedback(tenant_id=tenant, thread_id=thread, rating="up")

    await fx.worker.run_once()
    rows = await fx.candidates.list_for_review(tenant_id=tenant)
    assert rows[0].signal == "positive_feedback"
    assert rows[0].feedback_rating == "up"


@pytest.mark.asyncio
async def test_plain_success_without_feedback_is_skipped() -> None:
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="success")

    detected = await fx.worker.run_once()
    assert detected == 0
    assert await fx.candidates.list_for_review(tenant_id=tenant) == []


@pytest.mark.asyncio
async def test_negative_feedback_outranks_failed_outcome() -> None:
    """A 👎 is the most actionable signal even on an already-failed run."""
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="failed")
    await fx.seed_feedback(tenant_id=tenant, thread_id=thread, rating="down")

    await fx.worker.run_once()
    rows = await fx.candidates.list_for_review(tenant_id=tenant)
    assert rows[0].signal == "negative_feedback"


@pytest.mark.asyncio
async def test_trajectory_without_thread_meta_is_skipped() -> None:
    """No agent identity → cannot scope an agent-level dataset → skip."""
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="failed")

    detected = await fx.worker.run_once()
    assert detected == 0


@pytest.mark.asyncio
async def test_candidate_carries_agent_scope() -> None:
    fx = _Fixture()
    tenant, thread, user = uuid4(), uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread, agent_name="auditor", user_id=user)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="failed", user_id=user)

    await fx.worker.run_once()
    rows = await fx.candidates.list_for_review(tenant_id=tenant)
    assert rows[0].agent_name == "auditor"
    assert rows[0].agent_version == "1.0.0"
    assert rows[0].user_id == user


@pytest.mark.asyncio
async def test_rescan_is_idempotent() -> None:
    fx = _Fixture()
    tenant, thread = uuid4(), uuid4()
    await fx.seed_thread(tenant_id=tenant, thread_id=thread)
    await fx.seed_trajectory(tenant_id=tenant, thread_id=thread, outcome="failed")

    assert await fx.worker.run_once() == 1
    assert await fx.worker.run_once() == 0
    assert len(await fx.candidates.list_for_review(tenant_id=tenant)) == 1


@pytest.mark.asyncio
async def test_start_stop_lifecycle() -> None:
    fx = _Fixture()
    assert fx.worker.is_running is False
    fx.worker.start()
    assert fx.worker.is_running is True
    await fx.worker.stop()
    assert fx.worker.is_running is False
