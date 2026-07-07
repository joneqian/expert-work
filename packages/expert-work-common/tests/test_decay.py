"""Unit tests for temporal decay weighting (Stream CM-6, Mini-ADR CM-G3)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from expert_work.common.search import DECAY_FLOOR, temporal_decay_factor


def test_zero_age_is_full_weight() -> None:
    assert temporal_decay_factor(age=timedelta(0)) == pytest.approx(1.0)


def test_one_half_life_decays_the_decaying_half() -> None:
    # 0.5 + 0.5 * 2^-1 = 0.75
    assert temporal_decay_factor(age=timedelta(days=30)) == pytest.approx(0.75)


def test_two_half_lives() -> None:
    # 0.5 + 0.5 * 2^-2 = 0.625
    assert temporal_decay_factor(age=timedelta(days=60)) == pytest.approx(0.625)


def test_ancient_age_approaches_floor_never_below() -> None:
    # 2^-(3650/30) underflows to 0.0 in float — the factor lands exactly
    # on the floor, never under it.
    factor = temporal_decay_factor(age=timedelta(days=3650))
    assert factor >= DECAY_FLOOR
    assert factor == pytest.approx(DECAY_FLOOR, abs=1e-6)


def test_negative_age_clamps_to_zero() -> None:
    # Clock skew — last_used_at in the future must not boost above 1.0.
    assert temporal_decay_factor(age=timedelta(days=-5)) == pytest.approx(1.0)


def test_custom_half_life() -> None:
    assert temporal_decay_factor(
        age=timedelta(days=1), half_life=timedelta(days=1)
    ) == pytest.approx(0.75)


def test_monotonically_non_increasing() -> None:
    ages = [timedelta(days=d) for d in (0, 1, 7, 30, 90, 365)]
    factors = [temporal_decay_factor(age=a) for a in ages]
    assert factors == sorted(factors, reverse=True)
