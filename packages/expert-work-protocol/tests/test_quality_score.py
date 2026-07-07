"""Unit tests for :class:`QualityScoreRecord` — Stream RT-5 (RT-ADR-24)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from expert_work.protocol import QualityScoreRecord


def test_record_carries_verdict_and_dimensions() -> None:
    rec = QualityScoreRecord(
        tenant_id=uuid4(),
        agent_name="support-bot",
        agent_version="1",
        run_id=uuid4(),
        thread_id=uuid4(),
        overall=4,
        dimensions={"addressed_request": 5, "coherence": 4, "safety": 5},
        rationale="Answered the refund question clearly.",
        judge_model="claude-haiku-4-5-20251001",
    )
    assert rec.overall == 4
    assert rec.dimensions["addressed_request"] == 5
    # Store-populated fields default to None until persisted.
    assert rec.id is None
    assert rec.observed_at is None


def test_record_is_frozen() -> None:
    rec = QualityScoreRecord(
        tenant_id=uuid4(),
        agent_name="a",
        agent_version="-",
        run_id=uuid4(),
        thread_id=uuid4(),
        overall=3,
        dimensions={},
        rationale="",
        judge_model="m",
    )
    with pytest.raises(ValidationError):
        rec.overall = 5
