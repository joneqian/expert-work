"""Unit tests for :mod:`control_plane.eval_engine` — P1-S2.1c.

Covers the pure ``CapabilityReport -> EvalCaseOutcome`` mapping and the
suite guard. Actually running ``run_baseline`` (minutes, heavy graph) is
left to the eval harness's own tests / the weekly baseline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from control_plane.eval_engine import RunBaselineEvalEngine, reports_to_outcomes


@dataclass
class _StubReport:
    status: str
    aggregate_score: Mapping[str, float]
    session_metrics: Mapping[str, float] = field(default_factory=dict)


def test_reports_to_outcomes_maps_status_and_scores() -> None:
    reports = {
        "J.1_plan_execute": _StubReport("PASS", {"pass_rate": 1.0, "judge_mean": 4.5}),
        "J.2_reflect": _StubReport("FAIL", {"correction_rate": 0.5}),
        "J.6_multimodal": _StubReport("DEFERRED", {}),
    }

    outcomes = reports_to_outcomes(reports)

    by_cap = {o.capability: o for o in outcomes}
    assert by_cap["J.1_plan_execute"].passed is True
    assert by_cap["J.1_plan_execute"].scores == {"pass_rate": 1.0, "judge_mean": 4.5}
    assert by_cap["J.1_plan_execute"].case_id == "J.1_plan_execute"
    # FAIL and DEFERRED are both not-passed (deferred is not a green gate).
    assert by_cap["J.2_reflect"].passed is False
    assert by_cap["J.6_multimodal"].passed is False
    # No session_metrics on the stub → persisted column stays null.
    assert by_cap["J.1_plan_execute"].session_metrics is None


def test_reports_to_outcomes_threads_session_metrics() -> None:
    reports = {
        "J.1_plan_execute": _StubReport(
            "PASS", {"pass_rate": 1.0}, {"goal_completion": 0.8, "escalation_rate": 0.1}
        ),
    }

    outcomes = reports_to_outcomes(reports)

    assert outcomes[0].session_metrics == {"goal_completion": 0.8, "escalation_rate": 0.1}


@pytest.mark.asyncio
async def test_run_rejects_unknown_suite() -> None:
    engine = RunBaselineEvalEngine()
    with pytest.raises(ValueError, match="only runs 'm0_baseline'"):
        await engine.run("adversarial")
