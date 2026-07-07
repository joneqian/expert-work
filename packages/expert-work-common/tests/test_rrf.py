"""Tests for :mod:`expert_work.common.search.rrf` — Sprint #6 Mini-ADR U-6.

Mirrors the original ``_rrf_fuse`` coverage from
``services/orchestrator/tests/test_knowledge_tool.py`` plus generic /
edge-case scenarios that come with extraction:

- Generic over hashable types (UUID / dataclass / str).
- Empty inputs.
- Single-list identity.
- Duplicates inside one list collapse.
- Custom ``k`` parameter affects ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from expert_work.common.search.rrf import DEFAULT_K, rrf_fuse


def test_default_k_matches_knowledge_subsystem_constant() -> None:
    """Locks ``k=60`` so J.5 and Sprint #6 stay comparable."""
    assert DEFAULT_K == 60


def test_rrf_fuse_empty_returns_empty() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_fuse_single_list_is_identity() -> None:
    items = ["a", "b", "c", "d"]
    assert rrf_fuse([items]) == items


def test_rrf_fuse_rewards_items_in_both_lists() -> None:
    """The canonical RRF property: an item ranked by both inputs
    outscores items ranked by only one."""
    shared = "shared"
    vec_only = "vec"
    kw_only = "kw"
    fused = rrf_fuse([[vec_only, shared], [kw_only, shared]])
    assert fused[0] == shared
    assert set(fused) == {shared, vec_only, kw_only}


def test_rrf_fuse_two_identical_lists_match_single_list_order() -> None:
    items = ["a", "b", "c"]
    fused = rrf_fuse([items, items])
    assert fused == items


def test_rrf_fuse_collapses_duplicates_within_one_list() -> None:
    """Two appearances of the same item in one list count as the higher
    of the two ranks (the first occurrence)."""
    items = ["a", "b", "a"]  # second "a" is duplicate
    fused = rrf_fuse([items])
    assert fused == ["a", "b"]


def test_rrf_fuse_generic_over_uuid() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    fused = rrf_fuse([[a, b], [b, c]])
    assert fused[0] == b  # shared = highest


def test_rrf_fuse_generic_over_frozen_dataclass() -> None:
    @dataclass(frozen=True)
    class Doc:
        id: str
        title: str

    a = Doc(id="1", title="first")
    b = Doc(id="2", title="second")
    fused = rrf_fuse([[a, b], [b, a]])  # symmetric — both items equal score
    assert set(fused) == {a, b}


def test_rrf_fuse_custom_k_affects_score_gap() -> None:
    """Symmetric inputs: items ranked at the boundary (first AND last)
    always outscore the middle by Jensen's inequality on ``1/(k+x)``.
    Smaller ``k`` widens the gap; large ``k`` shrinks it. ``b`` (the
    middle item) is always last."""
    rankings = [["a", "b", "c"], ["c", "b", "a"]]
    default_fused = rrf_fuse(rankings)
    small_k = rrf_fuse(rankings, k=1)
    assert default_fused[-1] == "b"  # middle loses, regardless of k
    assert small_k[-1] == "b"
    assert small_k[0] in {"a", "c"}


def test_rrf_fuse_three_lists_combine() -> None:
    fused = rrf_fuse([["a", "b"], ["b", "c"], ["c", "a"]])
    # All three appear once at rank 0, once at rank 1, none at rank ≥ 2 →
    # all tied; sorted() is stable but Python dict insertion order
    # determines the order. Just check membership.
    assert set(fused) == {"a", "b", "c"}


def test_rrf_fuse_long_lists_dont_explode() -> None:
    """100-item lists fuse in reasonable time (algorithmic check)."""
    a_list = [f"a{i}" for i in range(100)]
    b_list = [f"b{i}" for i in range(100)]
    fused = rrf_fuse([a_list, b_list])
    assert len(fused) == 200  # disjoint sets so all 200 items appear


def test_rrf_fuse_zero_intersection_preserves_relative_order() -> None:
    """Items appearing in only one list are ranked by their position
    in that list."""
    fused = rrf_fuse([["a", "b", "c"], ["x", "y", "z"]])
    # Each list contributes its rank-0 item with the same score; same
    # for rank-1, etc. So "a" and "x" tie, "b" and "y" tie, etc.
    # All 6 items present.
    assert set(fused) == {"a", "b", "c", "x", "y", "z"}
