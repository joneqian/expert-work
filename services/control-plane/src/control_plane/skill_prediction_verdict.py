"""Prediction-falsification judge (Stream SE, SE-11 / Mini-ADR SE-A18/A19).

Borrowed from agentic-harness-engineering's Change-Manifest discipline: a
promoted skill version carried a *prediction* — the replay said it would lift
the score from ``baseline_score`` (no skill) to ``skill_score`` (with skill).
After promotion, production tells us the *realized* success rate. This judge
labels how much of that predicted gain actually materialized.

Why this reuses the replay evidence as the prediction (Mini-ADR SE-A18): the
replay-derived two-point estimate (baseline vs with-skill) is a harder,
already-persisted prediction than an LLM self-report; the ``skill_eval_result``
row IS the prediction, so no redundant table. The LLM-generator self-prediction
source is deferred to SE-11b.

Why it's diagnostic, not a gate (Mini-ADR SE-A19): the verdict NEVER decides
archive on its own — :func:`decide_rollback` (real outcomes + binomial test)
remains the only down-gate. The verdict is computed in the SAME rollback sweep
(叠加不替代), surfaced for review + fed back to co-evolve, and a ``HARMFUL``
verdict is an actionable signal but the actual archive still flows through the
binomial rollback judge. This keeps "never trust a model's self-assessment as
the promotion/demotion gate" intact.

Pure decision logic — no IO. The realized fraction measures how much of the
predicted ``delta = skill_score - baseline_score`` survived into production:
``realized_fraction = (observed_rate - baseline_score) / delta``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "PredictionVerdictAction",
    "PredictionVerdictConfig",
    "PredictionVerdictResult",
    "decide_prediction_verdict",
]


class PredictionVerdictAction(StrEnum):
    EFFECTIVE = "effective"  # ≥ effective_frac of the predicted gain held
    PARTIALLY_EFFECTIVE = "partially_effective"  # some of the gain held
    INEFFECTIVE = "ineffective"  # gain did not materialize (≈ no-skill level)
    MIXED = "mixed"  # below no-skill baseline but above the harm floor
    HARMFUL = "harmful"  # net-harmful (below absolute floor)
    INSUFFICIENT = "insufficient"  # n < n_min — can't judge yet


@dataclass(frozen=True)
class PredictionVerdictConfig:
    """Bands on the realized fraction of the predicted gain."""

    n_min: int = 6  # min window runs before judging (mirrors RollbackConfig)
    absolute_floor: float = 0.5  # observed below this → HARMFUL backstop
    effective_frac: float = 0.8  # ≥ this fraction of the gain held → EFFECTIVE
    partial_frac: float = 0.3  # ≥ this → PARTIALLY_EFFECTIVE
    ineffective_frac: float = -0.2  # ≥ this (but < partial) → INEFFECTIVE; below → MIXED


@dataclass(frozen=True)
class PredictionVerdictResult:
    action: PredictionVerdictAction
    predicted_delta: float  # skill_score - baseline_score (the prediction)
    realized_delta: float  # observed_rate - baseline_score
    realized_fraction: float  # realized_delta / predicted_delta (0.0 if delta≤0)
    n_window: int
    reason: str


def decide_prediction_verdict(
    *,
    baseline_score: float,
    skill_score: float,
    observed_rate: float,
    n_window: int,
    config: PredictionVerdictConfig | None = None,
) -> PredictionVerdictResult:
    """Label how much of the replay-predicted gain held up in production.

    Precedence:

    1. ``n_window < n_min`` → ``INSUFFICIENT`` (don't record a verdict yet).
    2. ``observed_rate < absolute_floor`` → ``HARMFUL`` (net-harmful backstop,
       mirrors the rollback floor).
    3. otherwise band the realized fraction of the predicted gain.

    ``predicted_delta ≤ 0`` is defensive — a promoted version always had a
    positive replay delta — and yields ``INEFFECTIVE`` with fraction 0.
    """
    cfg = config or PredictionVerdictConfig()
    predicted_delta = skill_score - baseline_score
    realized_delta = observed_rate - baseline_score
    fraction = realized_delta / predicted_delta if predicted_delta > 0 else 0.0

    if n_window < cfg.n_min:
        return PredictionVerdictResult(
            action=PredictionVerdictAction.INSUFFICIENT,
            predicted_delta=predicted_delta,
            realized_delta=realized_delta,
            realized_fraction=fraction,
            n_window=n_window,
            reason=f"n={n_window} < n_min={cfg.n_min}: not enough runs to judge",
        )

    if observed_rate < cfg.absolute_floor:
        action = PredictionVerdictAction.HARMFUL
        reason = (
            f"net-harmful: observed={observed_rate:.3f} < floor={cfg.absolute_floor} "
            f"(predicted lift {baseline_score:.3f}→{skill_score:.3f})"
        )
    elif predicted_delta <= 0:
        action = PredictionVerdictAction.INEFFECTIVE
        reason = f"no positive prediction to test (delta={predicted_delta:.3f})"
    elif fraction >= cfg.effective_frac:
        action = PredictionVerdictAction.EFFECTIVE
        reason = f"realized {fraction:.0%} of predicted gain (≥ {cfg.effective_frac:.0%})"
    elif fraction >= cfg.partial_frac:
        action = PredictionVerdictAction.PARTIALLY_EFFECTIVE
        reason = f"realized {fraction:.0%} of predicted gain"
    elif fraction >= cfg.ineffective_frac:
        action = PredictionVerdictAction.INEFFECTIVE
        reason = f"predicted gain did not materialize (realized {fraction:.0%}, ≈ no-skill level)"
    else:
        action = PredictionVerdictAction.MIXED
        reason = (
            f"below no-skill baseline but above floor "
            f"(observed={observed_rate:.3f}, baseline={baseline_score:.3f})"
        )

    return PredictionVerdictResult(
        action=action,
        predicted_delta=predicted_delta,
        realized_delta=realized_delta,
        realized_fraction=fraction,
        n_window=n_window,
        reason=reason,
    )
