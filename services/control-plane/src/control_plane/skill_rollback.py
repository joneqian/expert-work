"""Regression-rollback judge (Stream SE, SE-7d-2) — the symmetric down-gate.

SE-7a's ``decide_promotion`` puts a replay-verified DRAFT live; this decides
whether a *live* skill version has regressed enough in production to be auto-
archived. Pure decision logic (Mini-ADR SE-A11): the SE-7d-3 monitor feeds it a
rolling window of post-promotion outcomes + the promote-time success rate, and
acts on the verdict (``set_status(ARCHIVED)`` + feed the breaker).

Statistic — why NOT McNemar/Wilcoxon (SE-4a). Grounding is *paired*: the same
held-out case scored with vs without the skill. Rollback has no such pairing —
it compares a window of new runs against the scalar promote-time rate. The
correct test is therefore a one-sided exact **binomial** test: under H0 the
window successes ~ Binomial(n, baseline_rate); we roll back when the observed
rate is *significantly below* baseline (``p < alpha``) AND the drop clears an
effect-size floor. An **absolute floor** is a backstop: a skill promoted on a
weak baseline can regress to net-harmful without a statistically large *drop*,
so a window rate below ``absolute_floor`` rolls back regardless.

``cancelled`` runs are excluded from the sample — a user cancelling a run is
not the skill's fault, and counting it as a failure would bias toward false
rollbacks (which undo real learning).

Like SE-4a, the binomial CDF is hand-rolled (no scipy/numpy).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from helix_agent.protocol import TrajectoryOutcome

__all__ = [
    "RollbackAction",
    "RollbackConfig",
    "RollbackDecision",
    "binomial_cdf",
    "decide_rollback",
    "should_rollback",
]


class RollbackAction(StrEnum):
    ROLLBACK = "rollback"  # ACTIVE -> ARCHIVED (regressed)
    KEEP = "keep"  # within tolerance, leave ACTIVE
    INSUFFICIENT = "insufficient"  # n < n_min — can't judge yet, leave ACTIVE


@dataclass(frozen=True)
class RollbackConfig:
    """Thresholds for the rollback decision (CI-informed defaults)."""

    alpha: float = 0.05  # significance level for the one-sided binomial test
    theta_drop: float = 0.08  # min material drop vs baseline (~8pp)
    absolute_floor: float = 0.5  # window rate below this rolls back regardless
    n_min: int = 6  # min effective (non-cancelled) runs before judging


@dataclass(frozen=True)
class RollbackDecision:
    action: RollbackAction
    n_cases: int  # effective sample (cancelled excluded)
    observed_rate: float
    baseline_rate: float
    drop: float  # baseline_rate - observed_rate
    p_value: float  # P(X ≤ successes | n, baseline_rate)
    reason: str


def binomial_cdf(k: int, n: int, p: float) -> float:
    """``P(X ≤ k)`` for ``X ~ Binomial(n, p)``. Hand-rolled, no scipy.

    Returns ``0.0`` for ``k < 0`` and ``1.0`` for ``k ≥ n``.
    """
    upper = min(k, n)
    if upper < 0:
        return 0.0
    total = sum(math.comb(n, i) * (p**i) * ((1.0 - p) ** (n - i)) for i in range(upper + 1))
    return min(1.0, total)


def decide_rollback(
    outcomes: Sequence[TrajectoryOutcome],
    *,
    promote_baseline: float,
    config: RollbackConfig | None = None,
) -> RollbackDecision:
    """Decide whether a live skill version has regressed enough to archive.

    Precedence:

    1. effective ``n < n_min`` → ``INSUFFICIENT`` (leave ACTIVE; need more data).
    2. ``p < alpha`` ∧ ``drop ≥ theta_drop`` → ``ROLLBACK`` (significant regression).
    3. ``observed_rate < absolute_floor`` → ``ROLLBACK`` (net-harmful backstop).
    4. otherwise → ``KEEP``.
    """
    cfg = config or RollbackConfig()
    effective = [o for o in outcomes if o != "cancelled"]
    n = len(effective)
    successes = sum(1 for o in effective if o == "success")
    observed = successes / n if n else 0.0
    drop = promote_baseline - observed

    if n < cfg.n_min:
        return RollbackDecision(
            action=RollbackAction.INSUFFICIENT,
            n_cases=n,
            observed_rate=observed,
            baseline_rate=promote_baseline,
            drop=drop,
            p_value=1.0,
            reason=f"n={n} < n_min={cfg.n_min}: not enough runs to judge",
        )

    p_value = binomial_cdf(successes, n, promote_baseline)

    if p_value < cfg.alpha and drop >= cfg.theta_drop:
        action = RollbackAction.ROLLBACK
        reason = (
            f"regressed: rate={observed:.3f} vs baseline={promote_baseline:.3f} "
            f"(drop={drop:.3f} ≥ {cfg.theta_drop}, p={p_value:.4f} < {cfg.alpha})"
        )
    elif observed < cfg.absolute_floor:
        action = RollbackAction.ROLLBACK
        reason = (
            f"below absolute floor: rate={observed:.3f} < {cfg.absolute_floor} "
            f"(net-harmful backstop; baseline={promote_baseline:.3f})"
        )
    else:
        action = RollbackAction.KEEP
        reason = (
            f"within tolerance: rate={observed:.3f}, baseline={promote_baseline:.3f}, "
            f"drop={drop:.3f}, p={p_value:.4f}"
        )

    return RollbackDecision(
        action=action,
        n_cases=n,
        observed_rate=observed,
        baseline_rate=promote_baseline,
        drop=drop,
        p_value=p_value,
        reason=reason,
    )


def should_rollback(decision: RollbackDecision) -> bool:
    """Convenience predicate for the SE-7d-3 monitor's archive step."""
    return decision.action is RollbackAction.ROLLBACK
