"""Unit tests for the shared eval protocol — Stream J.13a."""

from __future__ import annotations

import sys
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    session_metrics_from_cases,
)


def test_deferred_helper_produces_zero_sample_empty_score() -> None:
    """A deferred capability writes its threshold but no scores."""
    report = CapabilityReport.deferred(
        capability="J.test",
        metric_type="pass-rate",
        threshold={"pass_rate": 0.8},
        deferred_reason="not yet shipped",
    )
    assert report.status == "DEFERRED"
    assert report.sample_size == 0
    assert report.aggregate_score == {}
    assert report.threshold == {"pass_rate": 0.8}
    assert report.deferred_reason == "not yet shipped"
    assert report.per_case == ()


def test_case_result_defaults_are_immutable() -> None:
    """``CapabilityCaseResult`` is frozen + has stable defaults."""
    r = CapabilityCaseResult(case_id="x", passed=True)
    assert r.scores == {}
    assert r.notes == ()


def test_session_metrics_goal_completion_is_pass_fraction() -> None:
    """``goal_completion`` is the fraction of cases (sessions) that passed."""
    cases = [
        CapabilityCaseResult(case_id="a", passed=True),
        CapabilityCaseResult(case_id="b", passed=True),
        CapabilityCaseResult(case_id="c", passed=False),
    ]
    metrics = session_metrics_from_cases(cases)
    assert metrics["goal_completion"] == 2 / 3
    # No escalation signal on the cases → the metric is omitted, not zeroed.
    assert "escalation_rate" not in metrics


def test_session_metrics_empty_when_no_cases() -> None:
    """No per-case rows → empty rollup (persisted column stays null)."""
    assert session_metrics_from_cases([]) == {}


def test_session_metrics_escalation_only_when_signalled() -> None:
    """``escalation_rate`` is averaged only over cases carrying the signal."""
    cases = [
        CapabilityCaseResult(case_id="a", passed=True, scores={"escalated": 1.0}),
        CapabilityCaseResult(case_id="b", passed=True, scores={"escalated": 0.0}),
        CapabilityCaseResult(case_id="c", passed=False),  # no signal — excluded
    ]
    metrics = session_metrics_from_cases(cases)
    assert metrics["goal_completion"] == 2 / 3
    assert metrics["escalation_rate"] == 0.5
