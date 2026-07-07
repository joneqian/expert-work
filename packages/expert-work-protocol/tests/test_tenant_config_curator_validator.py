"""Sprint #4 (Mini-ADR U-28) — TenantConfigRecord cross-field validator.

The Pydantic model rejects records where ``skill_archive_days`` is not
strictly greater than ``skill_stale_days``. The DB CHECK in migration
0044 is the second line of defense; this test enforces the same
invariant client-side so admins fail fast.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from expert_work.protocol import TenantConfigRecord, TenantPlan

_NOW = datetime.now(UTC)
_TENANT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _make(**overrides: object) -> TenantConfigRecord:
    base: dict[str, object] = {
        "tenant_id": _TENANT,
        "display_name": "Acme",
        "plan": TenantPlan.FREE,
        "created_at": _NOW,
        "updated_at": _NOW,
        "updated_by": "tester",
    }
    base.update(overrides)
    return TenantConfigRecord(**base)  # type: ignore[arg-type]


def test_defaults_satisfy_invariant() -> None:
    record = _make()
    assert record.skill_stale_days == 30
    assert record.skill_archive_days == 90


def test_archive_strictly_greater_than_stale_rejects_equal() -> None:
    with pytest.raises(ValidationError, match=r"skill_archive_days"):
        _make(skill_stale_days=30, skill_archive_days=30)


def test_archive_strictly_greater_than_stale_rejects_smaller() -> None:
    with pytest.raises(ValidationError, match=r"skill_archive_days"):
        _make(skill_stale_days=60, skill_archive_days=45)


def test_thresholds_within_bounds_accept() -> None:
    record = _make(skill_stale_days=7, skill_archive_days=30)
    assert record.skill_stale_days == 7
    assert record.skill_archive_days == 30


def test_thresholds_below_min_reject() -> None:
    with pytest.raises(ValidationError):
        _make(skill_stale_days=0)
    with pytest.raises(ValidationError):
        _make(skill_archive_days=1)


def test_thresholds_above_max_reject() -> None:
    with pytest.raises(ValidationError):
        _make(skill_stale_days=400)
    with pytest.raises(ValidationError):
        _make(skill_archive_days=800)
