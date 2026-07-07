"""SE-16 (SE-A39) — 👎 trajectories join the contrastive failure side.

Exercises ``_TrajectoryEvidenceProvider`` over in-memory stores: a
thumbs-down candidate's trajectory (with the user's comment prefixed)
must appear in ``SkillEvidence.failures`` ahead of outcome-based sourcing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from control_plane.skill_evolution_wiring import _TrajectoryEvidenceProvider
from expert_work.persistence import InMemoryCurationCandidateStore
from expert_work.persistence.feedback_store import FeedbackRecord, InMemoryFeedbackStore
from expert_work.protocol import CurationCandidateRecord, CurationSignal, TrajectoryOutcome
from expert_work.runtime.storage import InMemoryObjectStore
from orchestrator.trajectory import TrajectoryReader, TrajectoryRecord, TrajectoryRecorder

_NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


def _candidate(
    *, tenant: UUID, thread: UUID, key: str, signal: CurationSignal, outcome: TrajectoryOutcome
) -> CurationCandidateRecord:
    return CurationCandidateRecord(
        id=uuid4(),
        tenant_id=tenant,
        agent_name="assistant",
        thread_id=thread,
        trajectory_key=key,
        outcome=outcome,
        signal=signal,
        detected_at=_NOW,
    )


@pytest.mark.asyncio
async def test_downvoted_trajectory_joins_failures_with_comment() -> None:
    tenant = uuid4()
    object_store = InMemoryObjectStore()
    recorder = TrajectoryRecorder(object_store=object_store)
    reader = TrajectoryReader(object_store=object_store)
    candidates = InMemoryCurationCandidateStore()
    feedback = InMemoryFeedbackStore()

    # The 👍 candidate under distillation.
    up_thread = uuid4()
    await recorder.record(
        TrajectoryRecord(
            thread_id=up_thread,
            tenant_id=tenant,
            outcome="success",
            messages=[HumanMessage(content="do the report"), AIMessage(content="done right")],
            finished_at=_NOW,
        )
    )
    (up_key,) = await reader.list_keys(tenant_id=tenant, outcome="success")
    up_candidate = _candidate(
        tenant=tenant, thread=up_thread, key=up_key, signal="positive_feedback", outcome="success"
    )

    # A same-agent 👎 on a *successful* run — the "false success" contrast —
    # with the user's own comment.
    down_thread = uuid4()
    await recorder.record(
        TrajectoryRecord(
            thread_id=down_thread,
            tenant_id=tenant,
            outcome="success",
            messages=[HumanMessage(content="do the report"), AIMessage(content="did it wrong")],
            finished_at=_NOW,
        )
    )
    down_key = next(
        k for k in await reader.list_keys(tenant_id=tenant, outcome="success") if k != up_key
    )
    await candidates.upsert(
        _candidate(
            tenant=tenant,
            thread=down_thread,
            key=down_key,
            signal="negative_feedback",
            outcome="success",
        )
    )
    await feedback.insert(
        FeedbackRecord(
            tenant_id=tenant,
            thread_id=down_thread,
            rating="down",
            comment="数字全是编的",
            actor_id="user@example.com",
        )
    )

    provider = _TrajectoryEvidenceProvider(
        reader, candidate_store=candidates, feedback_store=feedback
    )
    evidence = await provider(up_candidate)

    assert evidence.successes  # the candidate's own trajectory
    assert len(evidence.failures) >= 1
    assert "数字全是编的" in evidence.failures[0]
    assert "did it wrong" in evidence.failures[0]


@pytest.mark.asyncio
async def test_without_stores_failures_fall_back_to_outcomes() -> None:
    tenant, thread = uuid4(), uuid4()
    object_store = InMemoryObjectStore()
    recorder = TrajectoryRecorder(object_store=object_store)
    await recorder.record(
        TrajectoryRecord(
            thread_id=thread,
            tenant_id=tenant,
            outcome="success",
            messages=[HumanMessage(content="hi"), AIMessage(content="ok")],
            finished_at=_NOW,
        )
    )
    reader = TrajectoryReader(object_store=object_store)
    (key,) = await reader.list_keys(tenant_id=tenant, outcome="success")
    provider = _TrajectoryEvidenceProvider(reader)
    evidence = await provider(
        _candidate(
            tenant=tenant, thread=thread, key=key, signal="positive_feedback", outcome="success"
        )
    )
    assert evidence.successes
    assert evidence.failures == ()
