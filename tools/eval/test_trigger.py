"""Unit tests for the J.10 trigger eval — Stream J.10-step5 closeout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from trigger import (  # type: ignore[import-not-found]  # noqa: E402
    TriggerEvalCase,
    evaluate_set,
    load_cases,
)


def test_load_cases_parses_seventeen() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "trigger" / "m0_baseline.yaml")
    assert len(cases) == 17


@pytest.mark.asyncio
async def test_baseline_dataset_passes() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "trigger" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS"
    assert report.aggregate_score["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_unknown_scenario_marks_case_failed() -> None:
    """An unknown scenario id fails the case rather than crashing the run."""
    case = TriggerEvalCase(case_id="bogus", scenario="not_a_scenario")
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert not report.per_case[0].passed
    assert any("unknown scenario" in n for n in report.per_case[0].notes)
