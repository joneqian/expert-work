"""Tests for the SE-6d pure assembly helpers."""

from __future__ import annotations

from typing import Any

from control_plane.skill_evolution_assembly import (
    SIGNAL_TIER_CALIBRATED,
    SIGNAL_TIER_HARD,
    SIGNAL_TIER_UNVERIFIED,
    extract_task_prompt,
    first_user_message,
    is_screen_sampled,
    select_signal_tier,
)


def test_signal_tier_hard_when_verifier_present() -> None:
    assert select_signal_tier(has_hard_verifier=True, judge_calibrated=False) == SIGNAL_TIER_HARD


def test_signal_tier_calibrated_when_no_verifier_but_calibrated() -> None:
    tier = select_signal_tier(has_hard_verifier=False, judge_calibrated=True)
    assert tier == SIGNAL_TIER_CALIBRATED


def test_signal_tier_unverified_otherwise() -> None:
    tier = select_signal_tier(has_hard_verifier=False, judge_calibrated=False)
    assert tier == SIGNAL_TIER_UNVERIFIED


def test_extract_task_prompt_prefers_known_keys() -> None:
    assert extract_task_prompt({"prompt": "do X"}) == "do X"
    assert extract_task_prompt({"message": "hi"}) == "hi"
    assert extract_task_prompt({"unrelated": "nope"}) is None
    assert extract_task_prompt({"prompt": "   "}) is None


def test_first_user_message() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "  summarise this  "},
        {"role": "assistant", "content": "ok"},
    ]
    assert first_user_message(messages) == "summarise this"


def test_first_user_message_none_when_absent() -> None:
    assert first_user_message([{"role": "assistant", "content": "hi"}]) is None


# ---------------------------------------------------------------------------
# SE-16 (SE-A45) — deterministic screen sampling
# ---------------------------------------------------------------------------


def test_is_screen_sampled_bounds() -> None:
    assert is_screen_sampled("any-key", 0) is False
    assert is_screen_sampled("any-key", -5) is False
    assert is_screen_sampled("any-key", 100) is True
    assert is_screen_sampled("any-key", 150) is True


def test_is_screen_sampled_is_deterministic() -> None:
    """Same key + same rate always lands on the same side — a retry re-sweep
    cannot re-roll its way past the sample."""
    for key in (f"traj/{i}" for i in range(50)):
        first = is_screen_sampled(key, 30)
        assert all(is_screen_sampled(key, 30) is first for _ in range(3))


def test_is_screen_sampled_rate_roughly_holds() -> None:
    keys = [f"tenant-a/success/2026/07/02/{i}.jsonl" for i in range(2000)]
    hits = sum(1 for k in keys if is_screen_sampled(k, 50))
    assert 850 <= hits <= 1150  # ~50% with generous slack (hash, not RNG)
    subset = [k for k in keys if is_screen_sampled(k, 5)]
    # A 5% sample must be a strict subset of the 50% sample (bucket < pct).
    assert all(is_screen_sampled(k, 50) for k in subset)
