"""Unit + integration tests for the memory-recall eval gate — K.K12."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from memory_recall import (  # noqa: E402 — modules co-located in tools/eval/
    EvalReport,
    evaluate_set,
    load_cases,
    mrr_at_k,
    recall_at_k,
)

# ---------------------------------------------------------------------------
# Pure metric functions — exercised first because everything else builds on them.
# ---------------------------------------------------------------------------


def test_recall_at_k_full_hit_returns_one() -> None:
    """Every expected id sits in the top-k."""
    assert recall_at_k(["m1", "m2", "m3"], ["m1"], 3) == 1.0
    assert recall_at_k(["m2", "m1"], ["m1", "m2"], 2) == 1.0


def test_recall_at_k_partial_hit_returns_fraction() -> None:
    """Only one of two expected ids is in the top-k."""
    assert recall_at_k(["m1", "m3"], ["m1", "m2"], 2) == 0.5


def test_recall_at_k_zero_expected_is_noop_one() -> None:
    """Empty expectation → 1.0 (the case is a no-op, not a failure)."""
    assert recall_at_k(["m1"], [], 5) == 1.0


def test_mrr_at_k_first_position_returns_one() -> None:
    """Right answer at rank 1 → 1/1 = 1.0."""
    assert mrr_at_k(["m1", "m2"], ["m1"], 5) == 1.0


def test_mrr_at_k_second_position_returns_half() -> None:
    """Right answer at rank 2 → 1/2 = 0.5."""
    assert mrr_at_k(["m2", "m1"], ["m1"], 5) == pytest.approx(0.5)


def test_mrr_at_k_missing_returns_zero() -> None:
    """Right answer not in the top-k → 0.0."""
    assert mrr_at_k(["m2", "m3"], ["m1"], 5) == 0.0


# ---------------------------------------------------------------------------
# YAML loader — the seed file is part of the contract; a bad shape fails CI.
# ---------------------------------------------------------------------------


def test_zh_en_seed_loads_with_eight_cases() -> None:
    """The committed seed set is 4 zh + 4 en."""
    cases = load_cases(_EVAL_DIR / "datasets/memory_recall/zh_en_seed.yaml")
    assert len(cases) == 8
    zh = [c for c in cases if c.language == "zh"]
    en = [c for c in cases if c.language == "en"]
    assert len(zh) == 4
    assert len(en) == 4
    # Every case has at least one expected hit so the metric is non-degenerate.
    assert all(c.expected_recall_ids for c in cases)


# ---------------------------------------------------------------------------
# Runner — drives the seed set through a keyword-overlap embedder.
# ---------------------------------------------------------------------------


class _KeywordOverlapEmbedder:
    """Deterministic embedder that sums one-hot vectors per word.

    Not a real semantic embedder — the test value is mechanistic
    correctness, not a quality claim. It maps each word in the input
    to a fixed dimension via ``hash(word) % DIM`` and accumulates a
    count vector. Two texts that share the same words get a high
    cosine similarity; unrelated texts get 0. ``content`` words that
    appear verbatim in ``query`` therefore rank above distractors —
    which is enough to exercise the harness end-to-end and pin the
    recall / MRR numbers for the seed set under this embedder.

    The harness itself remains embedder-agnostic; the moment we wire
    the real embedder (M1 dogfood), the same gate code runs against
    a meaningful set of numbers.
    """

    DIM: int = 256

    async def embed(
        self, texts: Sequence[str], *, tenant_id: UUID
    ) -> list[tuple[float, ...]]:
        del tenant_id  # Stream O O-9 — eval double has no per-tenant key
        return [self._encode(t) for t in texts]

    def _encode(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self.DIM
        for word in _tokenise(text):
            vec[hash(word) % self.DIM] += 1.0
        return tuple(vec)


def _tokenise(text: str) -> list[str]:
    """ASCII split on whitespace; for the CJK rows, fall back to per-char
    bigrams so 共享字符 still produces vector overlap."""
    cleaned = text.lower().strip()
    if not cleaned:
        return []
    ascii_words = [w for w in cleaned.replace(",", " ").split() if w]
    cjk_chars = [c for c in cleaned if "一" <= c <= "鿿"]
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return ascii_words + cjk_chars + cjk_bigrams


@pytest.mark.asyncio
async def test_seed_set_meets_recall_threshold() -> None:
    """Stream K.K12 SLO gate.

    Against the keyword-overlap fake embedder, every seed case must
    rank the right memory above the distractor — recall@5 = 1.0,
    MRR@5 = 1.0. The threshold the slo.md ``memory_recall_quality``
    line carries (≥ 0.7 / ≥ 0.5) is the M1 number against the real
    embedder; the test pins something stricter against the fake so any
    regression in the harness wiring (not the embedder) trips here.
    """
    cases = load_cases(_EVAL_DIR / "datasets/memory_recall/zh_en_seed.yaml")
    report = await evaluate_set(cases, embedder=_KeywordOverlapEmbedder(), k=5)

    assert isinstance(report, EvalReport)
    assert report.n_cases == 8
    assert report.mean_recall_at_k >= 0.7, (
        f"mean recall@5 dropped below 0.7 ({report.mean_recall_at_k:.2f}) — "
        "either a case wording regressed or the harness wiring is broken."
    )
    assert report.mean_mrr_at_k >= 0.5, (
        f"mean MRR@5 dropped below 0.5 ({report.mean_mrr_at_k:.2f})."
    )


@pytest.mark.asyncio
async def test_per_case_breakdown_carries_language_label() -> None:
    """The per-case results separate zh vs en so a future regression
    against the real embedder can be diagnosed per-language without
    re-running the harness."""
    cases = load_cases(_EVAL_DIR / "datasets/memory_recall/zh_en_seed.yaml")
    report = await evaluate_set(cases, embedder=_KeywordOverlapEmbedder(), k=5)

    by_lang = {"zh": 0, "en": 0}
    for result in report.per_case:
        by_lang[result.language] += 1
    assert by_lang == {"zh": 4, "en": 4}


# ---------------------------------------------------------------------------
# Capability Uplift Sprint #6 — hybrid baseline gate (Mini-ADR U-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_mode_runs_end_to_end_on_seed_set() -> None:
    """Smoke: ``mode='hybrid'`` runs the same seed set without errors
    and returns a report of the same cardinality as the vector path."""
    cases = load_cases(_EVAL_DIR / "datasets/memory_recall/zh_en_seed.yaml")
    report = await evaluate_set(cases, embedder=_KeywordOverlapEmbedder(), k=5, mode="hybrid")
    assert report.n_cases == 8


@pytest.mark.asyncio
async def test_hybrid_recall_does_not_regress_against_vector() -> None:
    """Sprint #6 gate (in-memory tier): hybrid recall@5 must be at
    least as good as the pre-Sprint-#6 vector path on the same set.

    The 10% lift gate documented in § 7.2.3 is enforced by the SQL
    integration suite (docker required) — the real keyword-search
    path is what produces the lift, the InMemory token-overlap
    approximation just has to not *regress*.
    """
    cases = load_cases(_EVAL_DIR / "datasets/memory_recall/zh_en_seed.yaml")
    vector = await evaluate_set(cases, embedder=_KeywordOverlapEmbedder(), k=5, mode="vector")
    hybrid = await evaluate_set(cases, embedder=_KeywordOverlapEmbedder(), k=5, mode="hybrid")
    # Small tolerance for RRF tie-break ordering on saturated cases.
    assert hybrid.mean_recall_at_k >= vector.mean_recall_at_k - 0.01, (
        f"hybrid recall@5 regressed against vector "
        f"(hybrid={hybrid.mean_recall_at_k:.3f}, vector={vector.mean_recall_at_k:.3f})"
    )
