"""Unit tests for the J.9 Artifact eval — Stream J.13a closeout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from artifact import (  # type: ignore[import-not-found]  # noqa: E402
    ArtifactCase,
    evaluate_set,
    load_cases,
)


def test_load_cases_parses_sixteen() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "artifact" / "m0_baseline.yaml")
    assert len(cases) == 16


@pytest.mark.asyncio
async def test_baseline_dataset_passes() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "artifact" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS"
    assert report.aggregate_score["pass_rate"] == 1.0


@pytest.mark.asyncio
async def test_unknown_scenario_marks_case_failed() -> None:
    """A case with an unknown scenario id must fail rather than crash the run."""
    case = ArtifactCase(case_id="bogus", scenario="not_a_scenario")  # type: ignore[arg-type]
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert not report.per_case[0].passed
    assert any("unknown scenario" in n for n in report.per_case[0].notes)
