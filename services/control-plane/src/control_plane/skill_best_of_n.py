"""Best-of-N candidate diversity (Stream SE, SE-14 / Mini-ADR SE-A29..A33).

Borrowed from agentic-harness-engineering's Best-of-N evolve loop: instead of
one distilled draft refined sequentially, generate N drafts from *different
angles* (strategy hints), run each through the SAME replay gate, and pick the
winner by its :class:`GroundingDecision`. The winner then enters the existing
bounded co-evolve refinement (Best-of-N widens; co-evolve deepens).

This module is pure decision logic — no IO, no orchestrator import (keeps the
control-plane ↔ orchestrator boundary clean per
[memory:control-plane-lazy-import-orchestrator]). The processor adapts each
``GroundingDecision`` into a :class:`WinnerCandidate` before calling
:func:`pick_winner`; the winner choice never invents a new verdict — it only
ranks among already-grounded candidates (SE-A0 single收口 unchanged).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "BestOfNConfig",
    "DistillHint",
    "WinnerCandidate",
    "hint_instruction",
    "pick_winner",
]


class DistillHint(StrEnum):
    """A distillation angle. Diversifies the N drafts so Best-of-N explores
    more of the solution space than N identical generations would."""

    PROMPT_FOCUS = "prompt_focus"  # rewrite the how-to / guardrail wording
    TOOLS_FOCUS = "tools_focus"  # tighten tool selection + trigger conditions
    ANCHORED_EXAMPLES = "anchored_examples"  # embed type-level abstract examples


#: Instruction snippet appended to the distiller prompt per hint. Each only
#: steers *emphasis* — it never relaxes the distiller's two hard guards
#: (``allowed_tools`` cap + ``_looks_too_specific`` rejection).
_HINT_TEXT: dict[DistillHint, str] = {
    DistillHint.PROMPT_FOCUS: (
        "ANGLE: prioritise a crisp, well-sequenced how-to with explicit "
        "guardrails in prompt_fragment; keep tool_names to what the traces "
        "actually used."
    ),
    DistillHint.TOOLS_FOCUS: (
        "ANGLE: prioritise precise tool selection and trigger conditions in "
        "prompt_fragment (when to call which tool, in what order); keep the "
        "prose minimal. tool_names must stay within the agent's real tools."
    ),
    DistillHint.ANCHORED_EXAMPLES: (
        "ANGLE: embed 1-2 TYPE-LEVEL abstract examples in prompt_fragment to "
        "anchor the procedure. Examples MUST be schematic — no concrete values, "
        "IDs, paths, timestamps, or names."
    ),
}


def hint_instruction(hint: DistillHint | None) -> str:
    """The prompt snippet for a hint, or ``""`` when no hint is set."""
    return "" if hint is None else _HINT_TEXT[hint]


@dataclass(frozen=True)
class BestOfNConfig:
    """Best-of-N knobs. Empty ``hints`` (default) = Best-of-N OFF — the
    processor runs the single-draft path unchanged (opt-in, SE-A33)."""

    hints: tuple[DistillHint, ...] = ()
    max_parallel_drafts: int = 3


#: Higher rank wins. Mirrors :class:`SignalTier` string values (SE-A5b) without
#: importing the orchestrator enum; an unknown tier ranks 0 (falls back to the
#: numeric criteria below).
_TIER_RANK: dict[str, int] = {
    "hard_verifier": 3,
    "calibrated_judge": 2,
    "unverified": 1,
}


@dataclass(frozen=True)
class WinnerCandidate:
    """The fields of a candidate's ``GroundingDecision`` that the winner
    selection ranks on, plus a stable ``name`` for the deterministic tiebreak.
    The processor builds one per drafted angle."""

    name: str
    verdict: str  # EvalVerdict — only "pass" candidates are eligible
    auto_promote_eligible: bool
    signal_tier: str
    delta: float
    p_value: float
    n_cases: int
    index: int = field(default=0)


def pick_winner(candidates: Sequence[WinnerCandidate]) -> int | None:
    """Return the ``index`` of the winning candidate, or ``None`` when none
    grounded (the processor then falls back to the single-draft path — SE-14
    never fabricates a winner).

    Ranking among ``verdict == "pass"`` candidates (descending preference):
    auto-promote-eligible, then signal tier, then effect size (``delta``), then
    significance (lower ``p_value``), then sample size (``n_cases``), then
    ``name`` lexicographically — a fully deterministic, CI-assertable order.
    """
    eligible = [c for c in candidates if c.verdict == "pass"]
    if not eligible:
        return None

    def sort_key(c: WinnerCandidate) -> tuple[int, int, float, float, int]:
        return (
            1 if c.auto_promote_eligible else 0,
            _TIER_RANK.get(c.signal_tier, 0),
            c.delta,
            -c.p_value,
            c.n_cases,
        )

    # Highest numeric key wins; the lexicographically smallest name breaks ties.
    best = eligible[0]
    best_key = sort_key(best)
    for c in eligible[1:]:
        k = sort_key(c)
        if k > best_key or (k == best_key and c.name < best.name):
            best, best_key = c, k
    return best.index
