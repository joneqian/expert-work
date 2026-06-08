"""Tests for the SE-7d-2 regression-rollback judge (pure decision logic).

The rollback judge is the symmetric counterpart of SE-7a's ``decide_promotion``:
auto-promote puts a verified DRAFT live; this decides whether a live skill
*version* has regressed enough to be auto-archived.

Unlike SE-4a grounding (per-case *paired* baseline-vs-treatment, McNemar/
Wilcoxon), rollback is **one-sample**: a window of post-promotion outcomes vs
the scalar promote-time success rate. The correct test is a one-sided exact
**binomial** test (is the observed rate significantly *below* baseline), plus
an absolute floor as a backstop for skills promoted on a weak baseline.
``cancelled`` runs are excluded — a user cancelling is not the skill's fault.
"""

from __future__ import annotations

from control_plane.skill_rollback import (
    RollbackAction,
    RollbackConfig,
    binomial_cdf,
    decide_rollback,
)
from helix_agent.protocol import TrajectoryOutcome


def _window(*, success: int, failed: int, cancelled: int = 0) -> list[TrajectoryOutcome]:
    return (
        ["success"] * success + ["failed"] * failed + ["cancelled"] * cancelled  # type: ignore[return-value]
    )


def test_insufficient_sample_holds() -> None:
    # 3 effective runs < n_min=6 → can't judge yet, leave ACTIVE.
    decision = decide_rollback(_window(success=1, failed=2), promote_baseline=0.9)
    assert decision.action is RollbackAction.INSUFFICIENT
    assert decision.n_cases == 3


def test_healthy_window_keeps() -> None:
    # Observed ≈ baseline over a healthy sample → KEEP.
    decision = decide_rollback(_window(success=18, failed=2), promote_baseline=0.9)
    assert decision.action is RollbackAction.KEEP


def test_significant_regression_rolls_back() -> None:
    # Promoted at 0.9; production crashed to 0.4 over 20 runs → significant.
    decision = decide_rollback(_window(success=8, failed=12), promote_baseline=0.9)
    assert decision.action is RollbackAction.ROLLBACK
    assert decision.p_value < 0.05
    assert "regress" in decision.reason


def test_absolute_floor_rolls_back_marginal_skill() -> None:
    # Marginal baseline (0.45) means the drop to 0.40 is NOT significant, but the
    # observed rate is below the 0.5 floor → rolled back by the backstop.
    decision = decide_rollback(_window(success=8, failed=12), promote_baseline=0.45)
    assert decision.action is RollbackAction.ROLLBACK
    assert decision.p_value >= 0.05  # significance did NOT fire
    assert "floor" in decision.reason


def test_cancelled_runs_excluded_from_sample() -> None:
    # 6 real successes + 10 cancelled: cancelled drop out, leaving a clean
    # all-success window → KEEP (not a rollback, and n_cases counts only reals).
    decision = decide_rollback(_window(success=6, failed=0, cancelled=10), promote_baseline=0.9)
    assert decision.action is RollbackAction.KEEP
    assert decision.n_cases == 6


def test_small_drop_within_tolerance_keeps() -> None:
    # Baseline 0.9, observed ~0.85 over 20 — within the effect-size floor → KEEP.
    decision = decide_rollback(_window(success=17, failed=3), promote_baseline=0.9)
    assert decision.action is RollbackAction.KEEP


def test_config_overrides_thresholds() -> None:
    # A stricter floor flips a borderline KEEP to ROLLBACK.
    window = _window(success=12, failed=8)  # 0.6 observed
    assert decide_rollback(window, promote_baseline=0.62).action is RollbackAction.KEEP
    strict = RollbackConfig(absolute_floor=0.7)
    assert decide_rollback(window, promote_baseline=0.62, config=strict).action is (
        RollbackAction.ROLLBACK
    )


def test_binomial_cdf_basics() -> None:
    # P(X ≤ n) = 1; P(X ≤ -1) = 0; symmetric coin midpoint ≈ just over 0.5.
    assert binomial_cdf(10, 10, 0.5) == 1.0
    assert binomial_cdf(-1, 10, 0.5) == 0.0
    assert 0.5 < binomial_cdf(5, 10, 0.5) <= 0.65
