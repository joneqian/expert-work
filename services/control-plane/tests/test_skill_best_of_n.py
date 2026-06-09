"""Stream SE — SE-14 Best-of-N candidate diversity (Mini-ADR SE-A29..A33).

Pure substrate: the deterministic winner selection + the per-angle distillation
hints. The processor fan-out orchestration (run N, replay each, adopt winner)
is SE-14b; these tests pin the decision + generation pieces it composes.
"""

from __future__ import annotations

from control_plane.skill_best_of_n import (
    BestOfNConfig,
    DistillHint,
    WinnerCandidate,
    hint_instruction,
    pick_winner,
)


def _cand(
    name: str,
    *,
    verdict: str = "pass",
    eligible: bool = True,
    tier: str = "hard_verifier",
    delta: float = 0.1,
    p: float = 0.01,
    n: int = 10,
    index: int = 0,
) -> WinnerCandidate:
    return WinnerCandidate(
        name=name,
        verdict=verdict,
        auto_promote_eligible=eligible,
        signal_tier=tier,
        delta=delta,
        p_value=p,
        n_cases=n,
        index=index,
    )


def test_no_pass_returns_none() -> None:
    cands = [_cand("a", verdict="fail", index=0), _cand("b", verdict="inconclusive", index=1)]
    assert pick_winner(cands) is None


def test_only_pass_candidates_eligible() -> None:
    cands = [
        _cand("a", verdict="fail", delta=0.9, index=0),  # high delta but not pass
        _cand("b", verdict="pass", delta=0.1, index=1),
    ]
    assert pick_winner(cands) == 1


def test_auto_promote_eligible_outranks_delta() -> None:
    cands = [
        _cand("a", eligible=False, delta=0.9, index=0),
        _cand("b", eligible=True, delta=0.1, index=1),
    ]
    assert pick_winner(cands) == 1


def test_higher_tier_outranks_delta() -> None:
    cands = [
        _cand("a", tier="unverified", delta=0.9, index=0),
        _cand("b", tier="hard_verifier", delta=0.1, index=1),
    ]
    assert pick_winner(cands) == 1


def test_delta_then_pvalue_then_n() -> None:
    cands = [
        _cand("a", delta=0.2, index=0),
        _cand("b", delta=0.3, index=1),  # highest delta
        _cand("c", delta=0.2, index=2),
    ]
    assert pick_winner(cands) == 1


def test_name_breaks_ties_deterministically() -> None:
    # All identical numeric keys → smallest name wins.
    cands = [_cand("zeta", index=0), _cand("alpha", index=1), _cand("mu", index=2)]
    assert pick_winner(cands) == 1  # "alpha"


def test_unknown_tier_ranks_lowest_but_still_pass() -> None:
    cands = [_cand("a", tier="weird", delta=0.5, index=0)]
    assert pick_winner(cands) == 0


# --- distillation hints ---------------------------------------------------


def test_hint_instruction_empty_for_none() -> None:
    assert hint_instruction(None) == ""


def test_each_hint_has_distinct_instruction() -> None:
    texts = {hint_instruction(h) for h in DistillHint}
    assert len(texts) == len(list(DistillHint))
    assert all(t for t in texts)


def test_best_of_n_off_by_default() -> None:
    assert BestOfNConfig().hints == ()
