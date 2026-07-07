"""Tests for the G.4 eval harness (test matrix #68)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from expert_work_eval import (
    Assertion,
    EvalCase,
    EvalSet,
    evaluate_assertion,
    format_report,
    load_eval_set,
    mock_provider,
    run_eval,
)

_DATASETS = Path(__file__).parent / "datasets"


@pytest.mark.parametrize(
    ("atype", "value", "output", "expected"),
    [
        ("contains", "ok", "all ok here", True),
        ("contains", "no", "all ok here", False),
        ("not_contains", "ERROR", "all ok here", True),
        ("not_contains", "ok", "all ok here", False),
        ("equals", "exact", "exact", True),
        ("equals", "exact", "exact ", False),
        ("regex", r"\d+", "abc 42", True),
        ("regex", r"^\d+$", "abc 42", False),
    ],
)
def test_evaluate_assertion(atype: str, value: str, output: str, expected: bool) -> None:
    assert evaluate_assertion(Assertion(type=atype, value=value), output) is expected


def test_assertion_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown assertion type"):
        Assertion(type="sentiment", value="positive")


def test_run_eval_reports_pass_and_fail() -> None:
    eval_set = EvalSet(
        name="t",
        cases=(
            EvalCase(
                id="good",
                prompt="p1",
                mock_response="hello world",
                assertions=(Assertion(type="contains", value="hello"),),
            ),
            EvalCase(
                id="bad",
                prompt="p2",
                mock_response="hello world",
                assertions=(Assertion(type="contains", value="xyz"),),
            ),
        ),
    )
    report = asyncio.run(run_eval(eval_set, mock_provider(eval_set)))

    assert (report.total, report.passed, report.ok) == (2, 1, False)
    by_id = {r.case_id: r for r in report.results}
    assert by_id["good"].passed is True
    assert by_id["bad"].passed is False
    assert by_id["bad"].failures  # carries the failed-assertion description


def test_load_eval_set_parses_example() -> None:
    eval_set = load_eval_set(_DATASETS / "example.yaml")
    assert eval_set.name == "example-smoke"
    assert {c.id for c in eval_set.cases} == {
        "greeting-includes-name",
        "json-status-shape",
        "refusal-stays-safe",
    }


def test_example_eval_set_passes_in_mock_mode() -> None:
    """#68 — the bundled example set runs end to end and every case passes."""
    eval_set = load_eval_set(_DATASETS / "example.yaml")
    assert eval_set.cases  # non-empty
    report = asyncio.run(run_eval(eval_set, mock_provider(eval_set)))
    assert report.ok, format_report(report)
