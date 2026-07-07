"""Stream X — Skill.required_tier protocol field (X1; tenant_id nullability = X2)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from expert_work.protocol import Skill, TenantPlan


def _skill(**over: object) -> Skill:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "weekly-report",
        "status": "active",
        "latest_version": 1,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
    }
    base.update(over)
    return Skill(**base)  # type: ignore[arg-type]


def test_required_tier_defaults_free() -> None:
    """Existing skill rows (no required_tier) default to FREE — additive, no behavior change."""
    assert _skill().required_tier is TenantPlan.FREE


def test_required_tier_can_be_set() -> None:
    assert _skill(required_tier=TenantPlan.PRO).required_tier is TenantPlan.PRO
    assert _skill(required_tier=TenantPlan.ENTERPRISE).required_tier is TenantPlan.ENTERPRISE


def test_skill_frozen() -> None:
    import pytest
    from pydantic import ValidationError

    s = _skill()
    with pytest.raises(ValidationError):
        s.required_tier = TenantPlan.PRO
