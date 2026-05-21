"""Unit tests for the J.4 sub-agent eval — Stream J.13a."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from sub_agent import (  # type: ignore[import-not-found]  # noqa: E402
    ScriptedMessage,
    SubAgentCase,
    evaluate_set,
    load_cases,
)


def test_load_cases_parses_twelve() -> None:
    """8 base cases + 3 parallel_fanout + 1 cycle_detection (Mini-ADR J-40)."""
    cases = load_cases(_EVAL_DIR / "datasets" / "sub_agent" / "m0_baseline.yaml")
    assert len(cases) == 12


@pytest.mark.asyncio
async def test_baseline_dataset_passes() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "sub_agent" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS"
    assert report.aggregate_score["pass_rate"] >= 0.80


@pytest.mark.asyncio
async def test_contradictory_outcome_fails() -> None:
    """A case that lies about the expected outcome is marked failed."""
    case = SubAgentCase(
        case_id="contradictory",
        scenario="success",
        scripted_messages=(
            ScriptedMessage(role="human", content="task"),
            ScriptedMessage(role="ai", content="answer"),
        ),
        scripted_step_count=1,
        has_recorder=True,
        expected_outcome_label="success",
        # Wrong keyword — content is "answer" not "missing-kw".
        expected_content_contains="missing-kw",
        expected_iteration_used=1,
        expected_llm_call_count=1,
        expected_trajectory_dispatched=True,
    )
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert not report.per_case[0].passed


@pytest.mark.asyncio
async def test_recorder_off_skips_dispatch() -> None:
    """``has_recorder=False`` keeps the recorder out of the build, no dispatch."""
    case = SubAgentCase(
        case_id="no-rec",
        scenario="success",
        scripted_messages=(
            ScriptedMessage(role="human", content="task"),
            ScriptedMessage(role="ai", content="ok"),
        ),
        scripted_step_count=1,
        has_recorder=False,
        expected_outcome_label="success",
        expected_content_contains="ok",
        expected_iteration_used=1,
        expected_llm_call_count=1,
        expected_trajectory_dispatched=False,
    )
    report = await evaluate_set([case])
    assert report.status == "PASS"


@pytest.mark.asyncio
async def test_parallel_fanout_serial_fails() -> None:
    """Mini-ADR J-40 — if the eval ran calls serially, the case would fail.

    Sanity-checks the wall-clock guard: a serial bound of ``N * delay`` is
    exactly what a serial implementation would hit, and the eval rejects
    a wall-clock that lands at or above that bound. Setting
    ``parallel_max_wall_clock_s`` below the serial bound is what makes
    the case meaningful.
    """
    case = SubAgentCase(
        case_id="parallel-needs-real-concurrency",
        scenario="parallel_fanout",
        scripted_messages=(
            ScriptedMessage(role="human", content="x"),
            ScriptedMessage(role="ai", content="ok"),
        ),
        scripted_step_count=1,
        has_recorder=False,
        expected_outcome_label="success",
        expected_content_contains="ok",
        expected_iteration_used=1,
        expected_llm_call_count=1,
        parallel_count=2,
        parallel_child_delay_s=0.05,
        # Force-fail: 0.001s is below any real concurrency would hit either.
        parallel_max_wall_clock_s=0.001,
    )
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert not report.per_case[0].passed


@pytest.mark.asyncio
async def test_cycle_detection_acyclic_chain_fails() -> None:
    """Mini-ADR J-40 — a deliberately acyclic chain should not raise; the
    case marks ``passed=False`` because the eval expects an
    ``AgentFactoryError`` for a ``cycle_detection`` scenario.
    """
    case = SubAgentCase(
        case_id="acyclic-should-fail",
        scenario="cycle_detection",
        scripted_messages=(ScriptedMessage(role="human", content="unused"),),
        scripted_step_count=0,
        has_recorder=False,
        expected_outcome_label="success",
        expected_content_contains="cycle",
        expected_iteration_used=0,
        expected_llm_call_count=0,
        cycle_chain=("alpha",),  # < 2 nodes — no cycle possible
    )
    report = await evaluate_set([case])
    assert report.status == "FAIL"
