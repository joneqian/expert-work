"""Unit tests for the Stream N platform_admin eval."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from platform_admin import (  # noqa: E402
    ScopeCase,
    evaluate_set,
    load_cases,
)


def test_load_cases_parses_baseline() -> None:
    """The shipped baseline has 8 cases (4 tenant_admin + 4 system_admin)."""
    cases = load_cases(_EVAL_DIR / "datasets" / "platform_admin" / "m0_baseline.yaml")
    assert len(cases) == 8


@pytest.mark.asyncio
async def test_baseline_dataset_passes_with_full_rate() -> None:
    """Threshold is 1.00 — every case must classify correctly."""
    cases = load_cases(_EVAL_DIR / "datasets" / "platform_admin" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS", report
    assert report.aggregate_score["pass_rate"] == 1.0
    assert report.sample_size == 8


@pytest.mark.asyncio
async def test_tenant_admin_star_is_denied() -> None:
    """A non-system-admin asking for ``tenant_id=*`` must be refused."""
    report = await evaluate_set(
        [
            ScopeCase(
                case_id="manual-tenant-admin-star",
                is_system_admin=False,
                requested="star",
                expected="forbid_cross",
            )
        ]
    )
    assert report.status == "PASS"


@pytest.mark.asyncio
async def test_system_admin_star_returns_cross_tenant() -> None:
    """A system_admin's ``tenant_id=*`` resolves to ``CrossTenant``."""
    report = await evaluate_set(
        [
            ScopeCase(
                case_id="manual-system-admin-star",
                is_system_admin=True,
                requested="star",
                expected="cross",
            )
        ]
    )
    assert report.status == "PASS"
