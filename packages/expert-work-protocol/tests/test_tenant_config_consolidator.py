"""Sprint #7 (Mini-ADR U-38) — TenantConfigRecord MemoryConsolidator fields.

Tests that the 4 new memory_* fields land with correct defaults +
bounded ranges + are independently patchable (no cross-field invariant
between them, unlike the Sprint #4 skill_*_days pair).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from expert_work.protocol import TenantConfigPatch, TenantConfigRecord, TenantPlan

_NOW = datetime.now(UTC)
_TENANT = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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


def test_defaults_match_design() -> None:
    record = _make()
    assert record.memory_consolidation_min_cluster_size == 3
    assert record.memory_consolidation_similarity == 0.85
    assert record.memory_purge_enabled is True
    assert record.memory_purge_min_age_days == 30


def test_cluster_size_below_min_rejects() -> None:
    with pytest.raises(ValidationError):
        _make(memory_consolidation_min_cluster_size=1)


def test_cluster_size_above_max_rejects() -> None:
    with pytest.raises(ValidationError):
        _make(memory_consolidation_min_cluster_size=21)


def test_similarity_below_min_rejects() -> None:
    with pytest.raises(ValidationError):
        _make(memory_consolidation_similarity=0.5)


def test_similarity_above_max_rejects() -> None:
    with pytest.raises(ValidationError):
        _make(memory_consolidation_similarity=1.0)


def test_purge_days_below_min_rejects() -> None:
    with pytest.raises(ValidationError):
        _make(memory_purge_min_age_days=6)


def test_purge_days_above_max_rejects() -> None:
    with pytest.raises(ValidationError):
        _make(memory_purge_min_age_days=400)


def test_purge_disabled_does_not_invalidate_other_fields() -> None:
    record = _make(memory_purge_enabled=False, memory_purge_min_age_days=30)
    assert record.memory_purge_enabled is False
    assert record.memory_purge_min_age_days == 30


def test_patch_all_fields_independent() -> None:
    patch = TenantConfigPatch(
        memory_consolidation_min_cluster_size=5,
        memory_consolidation_similarity=0.9,
        memory_purge_enabled=False,
        memory_purge_min_age_days=60,
    )
    assert patch.memory_consolidation_min_cluster_size == 5
    assert patch.memory_consolidation_similarity == 0.9
    assert patch.memory_purge_enabled is False
    assert patch.memory_purge_min_age_days == 60


def test_patch_partial_legal() -> None:
    # Each field independently None-able.
    p1 = TenantConfigPatch(memory_consolidation_similarity=0.95)
    assert p1.memory_consolidation_similarity == 0.95
    assert p1.memory_purge_enabled is None
    p2 = TenantConfigPatch(memory_purge_enabled=False)
    assert p2.memory_purge_enabled is False
    assert p2.memory_consolidation_similarity is None
