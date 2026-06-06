"""Tests for the SE-4a grounding decision core (the 咽喉's judgment brain).

Pure logic: no LLM, no graph, no DB. Covers the paired-significance stats
(Mini-ADR SE-A5) and the signal-strength auto-promote gating (SE-A5b).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from orchestrator.evolution.grounding import (
    CaseOutcome,
    SignalTier,
    decide_grounding,
    mcnemar_exact_p,
    to_eval_result,
    wilcoxon_signed_rank_p,
)

# --------------------------------------------------------------------------- #
# Statistics primitives
# --------------------------------------------------------------------------- #


def test_mcnemar_no_discordant_pairs_is_one() -> None:
    assert mcnemar_exact_p(0, 0) == 1.0


def test_mcnemar_symmetric_discordance_is_one() -> None:
    assert mcnemar_exact_p(5, 5) == 1.0


def test_mcnemar_all_improvements_known_value() -> None:
    # b=6, c=0: n=6, two-sided exact = 2 * C(6,0) * 0.5^6 = 2/64 = 0.03125
    assert mcnemar_exact_p(6, 0) == pytest.approx(0.03125)


def test_mcnemar_n5_floor_above_alpha() -> None:
    # n=5 discordant, all one direction: 2 * 1/32 = 0.0625 — the exact
    # two-sided floor, which is why n_min must be >= 6 at alpha=0.05.
    assert mcnemar_exact_p(5, 0) == pytest.approx(0.0625)


def test_mcnemar_is_symmetric_in_args() -> None:
    assert mcnemar_exact_p(2, 9) == mcnemar_exact_p(9, 2)


def test_wilcoxon_empty_or_all_zero_is_one() -> None:
    assert wilcoxon_signed_rank_p([]) == 1.0
    assert wilcoxon_signed_rank_p([0.0, 0.0, 0.0]) == 1.0


def test_wilcoxon_n5_all_positive_floor() -> None:
    # n=5 non-zero diffs all positive: only the empty and full subsets reach
    # min(W+, W-) == 0, so 2/32 = 0.0625 (the two-sided exact floor).
    assert wilcoxon_signed_rank_p([0.2, 0.3, 0.4, 0.5, 0.6]) == pytest.approx(0.0625)


def test_wilcoxon_n6_all_positive_significant() -> None:
    # n=6 all positive: 2/64 = 0.03125 < 0.05.
    assert wilcoxon_signed_rank_p([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]) == pytest.approx(0.03125)


def test_wilcoxon_zeros_are_dropped() -> None:
    # Dropping the two zeros leaves the n=6 all-positive case.
    p = wilcoxon_signed_rank_p([0.0, 0.1, 0.2, 0.3, 0.0, 0.4, 0.5, 0.6])
    assert p == pytest.approx(0.03125)


def test_wilcoxon_symmetric_diffs_not_significant() -> None:
    assert wilcoxon_signed_rank_p([0.3, -0.3, 0.2, -0.2, 0.1, -0.1]) > 0.05


# --------------------------------------------------------------------------- #
# CaseOutcome validation
# --------------------------------------------------------------------------- #


def test_case_outcome_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError):
        CaseOutcome(case_id="c1", baseline_score=0.0, treatment_score=1.5)


def test_case_outcome_from_judge_scores_normalises() -> None:
    oc = CaseOutcome.from_judge_scores("c1", baseline=1, treatment=5)
    assert oc.baseline_score == pytest.approx(0.0)
    assert oc.treatment_score == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# decide_grounding — verdict
# --------------------------------------------------------------------------- #


def _improving(n: int, *, base: float = 0.2, treat: float = 0.9) -> list[CaseOutcome]:
    return [CaseOutcome(f"c{i}", base, treat) for i in range(n)]


def test_clear_improvement_passes_and_t1_auto_eligible() -> None:
    d = decide_grounding(_improving(8), signal_tier=SignalTier.HARD_VERIFIER)
    assert d.verdict == "pass"
    assert d.delta == pytest.approx(0.7)
    assert d.p_value < 0.05
    assert d.auto_promote_eligible is True


def test_high_risk_pass_is_never_auto_eligible() -> None:
    d = decide_grounding(_improving(8), signal_tier=SignalTier.HARD_VERIFIER, high_risk=True)
    assert d.verdict == "pass"
    assert d.auto_promote_eligible is False


def test_unverified_tier_pass_not_auto_eligible() -> None:
    d = decide_grounding(_improving(8), signal_tier=SignalTier.UNVERIFIED)
    assert d.verdict == "pass"
    assert d.auto_promote_eligible is False


def test_calibrated_tier_requires_anchors_and_calibration() -> None:
    tier = SignalTier.CALIBRATED_JUDGE
    ok = decide_grounding(
        _improving(8), signal_tier=tier, anchors_passed=True, judge_calibrated=True
    )
    no_anchor = decide_grounding(
        _improving(8), signal_tier=tier, anchors_passed=False, judge_calibrated=True
    )
    no_calib = decide_grounding(
        _improving(8), signal_tier=tier, anchors_passed=True, judge_calibrated=False
    )
    assert ok.verdict == "pass" and ok.auto_promote_eligible is True
    assert no_anchor.auto_promote_eligible is False
    assert no_calib.auto_promote_eligible is False


def test_too_few_cases_is_inconclusive() -> None:
    d = decide_grounding(_improving(5), signal_tier=SignalTier.HARD_VERIFIER)
    assert d.verdict == "inconclusive"
    assert "n_min" in d.reason
    assert d.auto_promote_eligible is False


def test_clear_harm_is_fail() -> None:
    outcomes = [CaseOutcome(f"c{i}", 0.9, 0.1) for i in range(8)]
    d = decide_grounding(outcomes, signal_tier=SignalTier.HARD_VERIFIER)
    assert d.verdict == "fail"
    assert d.delta < 0
    assert d.auto_promote_eligible is False


def test_small_delta_below_theta_is_inconclusive() -> None:
    # Significant-ish direction but effect size under theta_delta.
    outcomes = [CaseOutcome(f"c{i}", 0.50, 0.53) for i in range(10)]
    d = decide_grounding(outcomes, signal_tier=SignalTier.HARD_VERIFIER)
    assert d.verdict == "inconclusive"
    assert d.auto_promote_eligible is False


def test_new_failure_blocks_pass() -> None:
    # Net positive overall, but one previously-passing case regresses.
    outcomes = [CaseOutcome(f"c{i}", 0.2, 0.9) for i in range(7)]
    outcomes.append(CaseOutcome("reg", 0.9, 0.1))  # pass -> fail regression
    d = decide_grounding(outcomes, signal_tier=SignalTier.HARD_VERIFIER)
    assert d.new_failures == 1
    assert d.verdict != "pass"


def test_binary_outcomes_use_mcnemar() -> None:
    outcomes = [CaseOutcome(f"c{i}", 0.0, 1.0) for i in range(6)]
    d = decide_grounding(outcomes, signal_tier=SignalTier.HARD_VERIFIER)
    assert d.test == "mcnemar"
    assert d.verdict == "pass"


def test_ordinal_outcomes_use_wilcoxon() -> None:
    outcomes = [CaseOutcome.from_judge_scores(f"c{i}", baseline=2, treatment=5) for i in range(6)]
    d = decide_grounding(outcomes, signal_tier=SignalTier.HARD_VERIFIER)
    assert d.test == "wilcoxon"
    assert d.verdict == "pass"


# --------------------------------------------------------------------------- #
# to_eval_result
# --------------------------------------------------------------------------- #


def test_to_eval_result_maps_fields() -> None:
    d = decide_grounding(_improving(8), signal_tier=SignalTier.HARD_VERIFIER)
    rid = UUID("11111111-1111-1111-1111-111111111111")
    sid = UUID("22222222-2222-2222-2222-222222222222")
    tid = UUID("33333333-3333-3333-3333-333333333333")
    now = datetime(2026, 6, 7, tzinfo=UTC)
    res = to_eval_result(
        d,
        result_id=rid,
        tenant_id=tid,
        skill_id=sid,
        skill_version=3,
        replay_source="trajectory",
        created_at=now,
        high_risk=False,
        evolution_round=1,
    )
    assert res.id == rid
    assert res.tenant_id == tid
    assert res.skill_id == sid
    assert res.skill_version == 3
    assert res.baseline_score == pytest.approx(d.baseline_score)
    assert res.skill_score == pytest.approx(d.treatment_score)
    assert res.delta == pytest.approx(d.delta)
    assert res.n_cases == d.n_cases
    assert res.replay_source == "trajectory"
    assert res.verdict == "pass"
    assert res.evolution_round == 1
    assert res.created_at == now
