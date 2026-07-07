"""Unit tests for :class:`QualityDriftAlertRecord` — Stream RT-5 (RT-ADR-24)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from expert_work.protocol import QualityDriftAlertRecord


def test_record_carries_window_stats() -> None:
    rec = QualityDriftAlertRecord(
        tenant_id=uuid4(),
        agent_name="support-bot",
        recent_mean=3.1,
        baseline_mean=4.2,
        drift_pct=0.26,
        recent_count=12,
        baseline_count=80,
    )
    assert rec.recent_mean == 3.1
    assert rec.drift_pct == 0.26
    # Store-populated fields default to None until persisted.
    assert rec.id is None
    assert rec.detected_at is None


def test_record_is_frozen() -> None:
    rec = QualityDriftAlertRecord(
        tenant_id=uuid4(),
        agent_name="a",
        recent_mean=1.0,
        baseline_mean=2.0,
        drift_pct=0.5,
        recent_count=10,
        baseline_count=10,
    )
    with pytest.raises(ValidationError):
        rec.drift_pct = 0.9
