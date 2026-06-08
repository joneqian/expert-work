"""Tests for the SE-6d pure assembly helpers."""

from __future__ import annotations

from typing import Any

from control_plane.skill_evolution_assembly import (
    SIGNAL_TIER_CALIBRATED,
    SIGNAL_TIER_HARD,
    SIGNAL_TIER_UNVERIFIED,
    extract_task_prompt,
    first_user_message,
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
