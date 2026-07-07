"""Unit tests for the plan-tier entitlement primitive (Stream W, Mini-ADR W-3)."""

from __future__ import annotations

import pytest

from expert_work.protocol import TIER_ORDER, TenantPlan, tier_satisfies


def test_tier_order_covers_every_plan() -> None:
    assert set(TIER_ORDER) == set(TenantPlan)


def test_strictly_increasing_order() -> None:
    assert (
        TIER_ORDER[TenantPlan.FREE] < TIER_ORDER[TenantPlan.PRO] < TIER_ORDER[TenantPlan.ENTERPRISE]
    )


@pytest.mark.parametrize(
    ("tenant", "required", "expected"),
    [
        (TenantPlan.FREE, TenantPlan.FREE, True),
        (TenantPlan.FREE, TenantPlan.PRO, False),
        (TenantPlan.FREE, TenantPlan.ENTERPRISE, False),
        (TenantPlan.PRO, TenantPlan.FREE, True),
        (TenantPlan.PRO, TenantPlan.PRO, True),
        (TenantPlan.PRO, TenantPlan.ENTERPRISE, False),
        (TenantPlan.ENTERPRISE, TenantPlan.FREE, True),
        (TenantPlan.ENTERPRISE, TenantPlan.PRO, True),
        (TenantPlan.ENTERPRISE, TenantPlan.ENTERPRISE, True),
    ],
)
def test_tier_satisfies(tenant: TenantPlan, required: TenantPlan, expected: bool) -> None:
    assert tier_satisfies(tenant, required) is expected
