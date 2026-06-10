"""Unit tests for greedy MMR selection (Stream CM-6, Mini-ADR CM-G4)."""

from __future__ import annotations

from helix_agent.common.search import mmr_select

_QUERY = (1.0, 0.0)


def test_near_duplicates_are_deduplicated() -> None:
    # Two near-identical candidates + an equally-relevant diverse one
    # (mirrored across the query axis). Pure relevance order is
    # dup_a, dup_b; round two penalizes dup_b's redundancy vs dup_a
    # (0.7·0.9 - 0.3·1.0 ≈ 0.33) so the diverse candidate
    # (0.7·0.9 - 0.3·0.62 ≈ 0.44) wins.
    query = (1.0, 0.0, 0.0)
    candidates = [
        ("dup_a", (0.9, 0.43589, 0.0)),
        ("dup_b", (0.9, 0.43589, 0.0001)),
        ("diverse", (0.9, -0.43589, 0.0)),
    ]
    assert mmr_select(query_embedding=query, candidates=candidates, k=2) == [
        "dup_a",
        "diverse",
    ]


def test_lambda_one_is_pure_relevance_order() -> None:
    candidates = [
        ("best", (1.0, 0.0)),
        ("twin", (1.0, 0.0)),
        ("worst", (0.0, 1.0)),
    ]
    assert mmr_select(query_embedding=_QUERY, candidates=candidates, k=3, lambda_=1.0) == [
        "best",
        "twin",
        "worst",
    ]


def test_ties_keep_caller_order() -> None:
    # Identical embeddings — the greedy argmax is strict (>) so the
    # earlier candidate (the caller's relevance order) wins the tie.
    candidates = [("first", (1.0, 0.0)), ("second", (1.0, 0.0))]
    assert mmr_select(query_embedding=_QUERY, candidates=candidates, k=1) == ["first"]


def test_k_at_least_pool_returns_everything() -> None:
    candidates = [("a", (1.0, 0.0)), ("b", (0.0, 1.0))]
    selected = mmr_select(query_embedding=_QUERY, candidates=candidates, k=10)
    assert sorted(selected) == ["a", "b"]


def test_dimension_mismatch_and_empty_embeddings_are_skipped() -> None:
    candidates = [
        ("good", (0.9, 0.1)),
        ("short", (1.0,)),
        ("empty", ()),
    ]
    assert mmr_select(query_embedding=_QUERY, candidates=candidates, k=3) == ["good"]


def test_zero_k_and_empty_candidates() -> None:
    assert mmr_select(query_embedding=_QUERY, candidates=[("a", (1.0, 0.0))], k=0) == []
    assert mmr_select(query_embedding=_QUERY, candidates=[], k=3) == []


def test_zero_norm_embedding_scores_zero_not_nan() -> None:
    candidates = [("zero", (0.0, 0.0)), ("real", (1.0, 0.0))]
    # Zero-vector matches the dimension so it stays in the pool, but its
    # similarity is 0 — the real vector must win.
    assert mmr_select(query_embedding=_QUERY, candidates=candidates, k=1) == ["real"]
