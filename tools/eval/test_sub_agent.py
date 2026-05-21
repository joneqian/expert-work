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


def test_load_cases_parses_eight() -> None:
    cases = load_cases(_EVAL_DIR / "datasets" / "sub_agent" / "m0_baseline.yaml")
    assert len(cases) == 8


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
