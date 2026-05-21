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
