"""Pure assembly helpers for the SE-6d evolution wiring.

The real providers / replay invoker (which touch the agent graph + LLM and can
only be validated in integration / the SE-9 benchmark) live in
``skill_evolution_wiring``. The decision-shaped logic that *can* be unit-tested
— signal-tier selection, pulling a task prompt out of a golden case or a
trajectory — lives here so it is covered in CI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "SIGNAL_TIER_CALIBRATED",
    "SIGNAL_TIER_HARD",
    "SIGNAL_TIER_UNVERIFIED",
    "extract_task_prompt",
    "first_user_message",
    "select_signal_tier",
]

# Mirror of orchestrator ``SignalTier`` values (kept as strings here to avoid an
# orchestrator import in this CI-tested module; the wiring layer maps them).
SIGNAL_TIER_HARD = "hard_verifier"
SIGNAL_TIER_CALIBRATED = "calibrated_judge"
SIGNAL_TIER_UNVERIFIED = "unverified"

_PROMPT_KEYS = ("prompt", "message", "input", "task", "question", "text")


def select_signal_tier(*, has_hard_verifier: bool, judge_calibrated: bool) -> str:
    """Pick the grounding signal tier (Mini-ADR SE-A5b).

    A hard verifier (golden case with checkable expectations) is T1; otherwise a
    calibrated judge is T2; an uncalibrated judge-only set is T3.
    """
    if has_hard_verifier:
        return SIGNAL_TIER_HARD
    if judge_calibrated:
        return SIGNAL_TIER_CALIBRATED
    return SIGNAL_TIER_UNVERIFIED


def extract_task_prompt(input_obj: Mapping[str, object]) -> str | None:
    """Pull a replay task prompt out of an eval-dataset ``input`` dict."""
    for key in _PROMPT_KEYS:
        value = input_obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def first_user_message(messages: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the first user-role message content of a ShareGPT trajectory."""
    for message in messages:
        if message.get("role") == "user":
            content = str(message.get("content", "")).strip()
            if content:
                return content
    return None
