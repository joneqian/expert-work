"""Grounding decision core for replay verification (Stream SE, SE-4a).

This module is the *judgment brain* of the self-evolution verification gate —
the 咽喉. Given per-case baseline-vs-treatment replay outcomes it decides
whether a candidate skill version is grounded (the ``verdict``) and, per the
signal-strength tiering (Mini-ADR SE-A5b), whether that verdict is strong
enough to auto-promote without human review.

It is intentionally **pure**: no LLM, no agent graph, no DB. Those live in the
SE-4b replay harness, which scores each replay case and feeds the outcomes
here. Keeping the most correctness-critical part of the loop pure makes it
fully unit-testable in CI.

Statistics (Mini-ADR SE-A5). A raw score delta is not enough — small samples
have wide confidence intervals (n~100 -> 95% CI half-width ~8-10pp), so a
candidate must clear a *paired significance* test as well as an effect-size
floor:

* **McNemar's exact test** for binary (assertion pass/fail) outcomes.
* **Wilcoxon signed-rank** for ordinal/continuous (judge-score) outcomes.

Both are hand-rolled (no scipy/numpy dependency). The two-sided exact p-value
cannot drop below 0.0625 at n=5, so ``n_min`` defaults to 6 — the true
significance floor at alpha=0.05 (the "T>=5" guideline is about *stability*,
not significance).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from helix_agent.protocol.skill import EvalVerdict, ReplaySource, SkillEvalResult

__all__ = [
    "CaseOutcome",
    "GroundingConfig",
    "GroundingDecision",
    "SignalTier",
    "decide_grounding",
    "mcnemar_exact_p",
    "to_eval_result",
    "wilcoxon_signed_rank_p",
]


class SignalTier(StrEnum):
    """Strength of the grounding signal (Mini-ADR SE-A5b).

    Determines whether a ``pass`` verdict may auto-promote. Persistence of the
    tier is deferred to SE-4b/SE-7; SE-4a only computes it into the decision.
    """

    HARD_VERIFIER = "hard_verifier"  # T1: assert / deterministic / golden eval
    CALIBRATED_JUDGE = "calibrated_judge"  # T2: rubric GenRM + anchors + agreement
    UNVERIFIED = "unverified"  # T3: judge only, no calibration anchor


@dataclass(frozen=True)
class CaseOutcome:
    """One replay case scored both ways, normalised to ``[0, 1]``."""

    case_id: str
    baseline_score: float
    treatment_score: float

    def __post_init__(self) -> None:
        for label, value in (
            ("baseline", self.baseline_score),
            ("treatment", self.treatment_score),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{label}_score for {self.case_id!r} must be in [0, 1], got {value}"
                )

    @classmethod
    def from_judge_scores(
        cls, case_id: str, *, baseline: int, treatment: int, scale_min: int = 1, scale_max: int = 5
    ) -> CaseOutcome:
        """Build from raw judge scores on an integer scale (default [1, 5])."""
        span = scale_max - scale_min
        if span <= 0:  # pragma: no cover — guards against a misconfigured scale
            raise ValueError("scale_max must be greater than scale_min")
        return cls(
            case_id=case_id,
            baseline_score=(baseline - scale_min) / span,
            treatment_score=(treatment - scale_min) / span,
        )


@dataclass(frozen=True)
class GroundingConfig:
    """Thresholds for the grounding decision (all CI-defaults; see SE-A5)."""

    alpha: float = 0.05  # significance level for the paired test
    theta_delta: float = 0.08  # min effect size on [0, 1] (~8pp, CI-informed)
    n_min: int = 6  # min paired cases - the exact two-sided floor at alpha=0.05
    pass_threshold: float = 0.5  # score ≥ threshold counts as a "pass" for a case


@dataclass(frozen=True)
class GroundingDecision:
    """Outcome of the grounding decision — pure numbers + verdict + gating."""

    verdict: EvalVerdict
    n_cases: int
    baseline_score: float  # mean over cases
    treatment_score: float  # mean over cases
    delta: float
    p_value: float
    test: Literal["mcnemar", "wilcoxon", "none"]
    new_failures: int
    signal_tier: SignalTier
    auto_promote_eligible: bool
    reason: str


# --------------------------------------------------------------------------- #
# Statistics primitives (hand-rolled, no scipy)
# --------------------------------------------------------------------------- #


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar (binomial) p-value over discordant pairs.

    ``b`` = baseline-fail / treatment-pass, ``c`` = baseline-pass /
    treatment-fail. Returns ``1.0`` when there are no discordant pairs.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5**n)
    return min(1.0, 2.0 * tail)


def _normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _wilcoxon_exact_two_sided(ranks: list[float], w_obs: float) -> float:
    """Exact two-sided p by enumerating all 2^n sign assignments."""
    n = len(ranks)
    total = sum(ranks)
    count = 0
    for mask in range(1 << n):
        w_plus = 0.0
        for i in range(n):
            if mask & (1 << i):
                w_plus += ranks[i]
        if min(w_plus, total - w_plus) <= w_obs + 1e-9:
            count += 1
    return min(1.0, count / (1 << n))


def wilcoxon_signed_rank_p(diffs: Sequence[float]) -> float:
    """Two-sided Wilcoxon signed-rank p-value for paired differences.

    ``diffs`` are ``treatment - baseline`` per case. Zeros are dropped. Uses
    the exact distribution for ``n ≤ 15`` and a normal approximation (with tie
    and continuity correction) above that. Returns ``1.0`` when no non-zero
    differences remain.
    """
    nonzero = [d for d in diffs if d != 0.0]
    n = len(nonzero)
    if n == 0:
        return 1.0

    order = sorted(range(n), key=lambda i: abs(nonzero[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(nonzero[order[j + 1]]) == abs(nonzero[order[i]]):
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # average of the 1-based ranks i+1..j+1
        for t in range(i, j + 1):
            ranks[order[t]] = avg_rank
        i = j + 1

    w_plus = sum(r for d, r in zip(nonzero, ranks, strict=True) if d > 0)
    w_minus = sum(r for d, r in zip(nonzero, ranks, strict=True) if d < 0)
    w_obs = min(w_plus, w_minus)

    if n <= 15:
        return _wilcoxon_exact_two_sided(ranks, w_obs)

    mean = n * (n + 1) / 4.0
    var = n * (n + 1) * (2 * n + 1) / 24.0
    if var == 0:  # pragma: no cover — only when all ranks are zero, impossible here
        return 1.0
    z = (w_obs - mean + 0.5) / math.sqrt(var)
    return min(1.0, 2.0 * _normal_cdf(z))


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


def _is_binary(outcomes: Sequence[CaseOutcome]) -> bool:
    return all(
        oc.baseline_score in (0.0, 1.0) and oc.treatment_score in (0.0, 1.0) for oc in outcomes
    )


def _auto_eligible(
    *,
    tier: SignalTier,
    high_risk: bool,
    anchors_passed: bool,
    judge_calibrated: bool,
) -> bool:
    if high_risk:
        return False  # high-risk always human-reviewed (SE-A0)
    if tier is SignalTier.HARD_VERIFIER:
        return True
    if tier is SignalTier.CALIBRATED_JUDGE:
        return anchors_passed and judge_calibrated
    return False  # UNVERIFIED


def decide_grounding(
    outcomes: Sequence[CaseOutcome],
    *,
    signal_tier: SignalTier,
    config: GroundingConfig | None = None,
    high_risk: bool = False,
    anchors_passed: bool = True,
    judge_calibrated: bool = True,
) -> GroundingDecision:
    """Decide whether a candidate skill version is grounded.

    Verdict rules (Mini-ADR SE-A5):

    * ``n < n_min`` → ``inconclusive`` (sample too small to be significant).
    * no new failures ∧ ``p < alpha`` ∧ ``delta ≥ theta`` → ``pass``.
    * ``p < alpha`` ∧ ``delta ≤ -theta`` → ``fail`` (significant harm).
    * otherwise → ``inconclusive``.

    Auto-promote eligibility (Mini-ADR SE-A5b) applies only to a ``pass``:
    high-risk is never eligible; ``HARD_VERIFIER`` always is; ``CALIBRATED_JUDGE``
    requires anchors to have passed and the judge to be calibrated;
    ``UNVERIFIED`` never is.
    """
    cfg = config or GroundingConfig()
    n = len(outcomes)

    baseline_mean = sum(oc.baseline_score for oc in outcomes) / n if n else 0.0
    treatment_mean = sum(oc.treatment_score for oc in outcomes) / n if n else 0.0
    delta = treatment_mean - baseline_mean

    new_failures = sum(
        1
        for oc in outcomes
        if oc.baseline_score >= cfg.pass_threshold and oc.treatment_score < cfg.pass_threshold
    )

    if n == 0:
        test: Literal["mcnemar", "wilcoxon", "none"] = "none"
        p_value = 1.0
    elif _is_binary(outcomes):
        test = "mcnemar"
        b = sum(
            1
            for oc in outcomes
            if oc.baseline_score < cfg.pass_threshold and oc.treatment_score >= cfg.pass_threshold
        )
        c = sum(
            1
            for oc in outcomes
            if oc.baseline_score >= cfg.pass_threshold and oc.treatment_score < cfg.pass_threshold
        )
        p_value = mcnemar_exact_p(b, c)
    else:
        test = "wilcoxon"
        p_value = wilcoxon_signed_rank_p(
            [oc.treatment_score - oc.baseline_score for oc in outcomes]
        )

    if n < cfg.n_min:
        verdict: EvalVerdict = "inconclusive"
        reason = f"n={n} < n_min={cfg.n_min}: sample too small to be significant"
    elif new_failures == 0 and p_value < cfg.alpha and delta >= cfg.theta_delta:
        verdict = "pass"
        reason = (
            f"grounded: delta={delta:.3f} ≥ {cfg.theta_delta}, p={p_value:.4f} < {cfg.alpha} "
            f"({test}), no new failures"
        )
    elif p_value < cfg.alpha and delta <= -cfg.theta_delta:
        verdict = "fail"
        reason = (
            f"significant harm: delta={delta:.3f} <= -{cfg.theta_delta}, p={p_value:.4f} ({test})"
        )
    else:
        verdict = "inconclusive"
        bits = []
        if new_failures:
            bits.append(f"{new_failures} new failure(s)")
        if p_value >= cfg.alpha:
            bits.append(f"p={p_value:.4f} ≥ {cfg.alpha}")
        if abs(delta) < cfg.theta_delta:
            bits.append(f"|delta|={abs(delta):.3f} < {cfg.theta_delta}")
        reason = "inconclusive: " + ("; ".join(bits) or "insufficient evidence")

    eligible = verdict == "pass" and _auto_eligible(
        tier=signal_tier,
        high_risk=high_risk,
        anchors_passed=anchors_passed,
        judge_calibrated=judge_calibrated,
    )

    return GroundingDecision(
        verdict=verdict,
        n_cases=n,
        baseline_score=baseline_mean,
        treatment_score=treatment_mean,
        delta=delta,
        p_value=p_value,
        test=test,
        new_failures=new_failures,
        signal_tier=signal_tier,
        auto_promote_eligible=eligible,
        reason=reason,
    )


def to_eval_result(
    decision: GroundingDecision,
    *,
    result_id: UUID,
    tenant_id: UUID | None,
    skill_id: UUID,
    skill_version: int,
    replay_source: ReplaySource,
    created_at: datetime,
    high_risk: bool = False,
    evolution_round: int = 0,
) -> SkillEvalResult:
    """Assemble a persistable ``SkillEvalResult`` from a grounding decision.

    ``result_id`` and ``created_at`` are injected by the caller (the SE-4b
    harness) so this stays pure and deterministic.
    """
    return SkillEvalResult(
        id=result_id,
        tenant_id=tenant_id,
        skill_id=skill_id,
        skill_version=skill_version,
        baseline_score=decision.baseline_score,
        skill_score=decision.treatment_score,
        delta=decision.delta,
        n_cases=decision.n_cases,
        replay_source=replay_source,
        verdict=decision.verdict,
        high_risk=high_risk,
        evolution_round=evolution_round,
        created_at=created_at,
    )
