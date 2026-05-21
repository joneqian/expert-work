"""Unit tests for the J.14 per-user isolation eval — Stream J.13a."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from per_user_isolation import (  # noqa: E402
    IsolationCase,
    evaluate_set,
    load_cases,
)

_USER_A = UUID("11111111-1111-1111-1111-111111111111")
_USER_B = UUID("22222222-2222-2222-2222-222222222222")


def test_load_cases_parses_twelve_cases() -> None:
    """The shipped baseline has 12 cases (3 legacy + 2 machine + 2 admin + 5 user)."""
    cases = load_cases(_EVAL_DIR / "datasets" / "per_user_isolation" / "m0_baseline.yaml")
    assert len(cases) == 12


@pytest.mark.asyncio
async def test_baseline_dataset_passes_with_full_rate() -> None:
    """Threshold is 1.00 — every case must classify correctly."""
    cases = load_cases(_EVAL_DIR / "datasets" / "per_user_isolation" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS"
    assert report.aggregate_score["pass_rate"] == 1.0
    assert report.sample_size == 12


@pytest.mark.asyncio
async def test_cross_user_with_no_admin_role_is_denied() -> None:
    """User A cannot read user B's owned thread."""
    case = IsolationCase(
        case_id="cross-user",
        meta_user_id=_USER_B,
        caller_user_id=_USER_A,
        principal_subject_type="user",
        principal_roles=(),
        expected_allowed=False,
    )
    report = await evaluate_set([case])
    assert report.status == "PASS"
    assert report.per_case[0].passed is True


@pytest.mark.asyncio
async def test_threshold_below_one_marks_fail() -> None:
    """Even a single wrong classification trips the 1.00 threshold."""
    # Wrong ``expected_allowed`` — caller IS user B reading own thread, but
    # case asserts deny. The eval reports FAIL because the implementation
    # correctly allows.
    case = IsolationCase(
        case_id="contradictory",
        meta_user_id=_USER_B,
        caller_user_id=_USER_B,
        principal_subject_type="user",
        principal_roles=(),
        expected_allowed=False,
    )
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert report.aggregate_score["pass_rate"] == 0.0


@pytest.mark.asyncio
async def test_admin_role_bypasses_user_check() -> None:
    """is_admin path returns True regardless of user_id mismatch."""
    case = IsolationCase(
        case_id="admin-bypass",
        meta_user_id=_USER_B,
        caller_user_id=_USER_A,
        principal_subject_type="user",
        principal_roles=("admin",),
        expected_allowed=True,
    )
    report = await evaluate_set([case])
    assert report.per_case[0].passed is True
