"""Auto-promote policy (Stream SE, SE-7a) — the governance gate.

Decides what happens to a grounded DRAFT skill (replay-verified by SE-4): does
it auto-promote to ACTIVE, go to human review, or just hold. This is the single
place "尽量全自动" is made safe — strong verification *plus* hard guardrails.

Pure decision logic (Mini-ADR SE-A10): every input is a plain flag computed
elsewhere — grounding + auto-promote eligibility (SE-4a, already bakes high-risk
+ signal-tier + anchors + calibration), the circuit breaker + rate limiter
(SE-7b), and the high-risk flag (SE-A0). The order encodes precedence: a
high-risk skill is *never* auto-promoted, even if everything else is green.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "PromoteAction",
    "PromoteDecision",
    "decide_promotion",
    "should_auto_promote",
]


class PromoteAction(StrEnum):
    AUTO_PROMOTE = "auto_promote"  # DRAFT -> ACTIVE without human review
    HUMAN_REVIEW = "human_review"  # stays DRAFT, surfaced for a human decision
    HOLD = "hold"  # not a promotion candidate (e.g. not grounded)


@dataclass(frozen=True)
class PromoteDecision:
    action: PromoteAction
    reason: str


def decide_promotion(
    *,
    grounded: bool,
    auto_promote_eligible: bool,
    high_risk: bool,
    breaker_open: bool,
    within_rate_limit: bool,
) -> PromoteDecision:
    """Decide a grounded DRAFT's fate (SE-A10).

    Precedence (hardest gate first):

    1. not grounded → ``HOLD`` (no replay-pass evidence; SE-A0).
    2. high-risk → ``HUMAN_REVIEW`` (never auto, even if otherwise eligible).
    3. circuit breaker open → ``HUMAN_REVIEW`` (degraded to all-human, SE-A12).
    4. not auto-promote-eligible → ``HUMAN_REVIEW`` (signal tier / anchors / etc).
    5. rate limit exceeded → ``HUMAN_REVIEW`` (defer, SE-A12).
    6. otherwise → ``AUTO_PROMOTE``.
    """
    if not grounded:
        return PromoteDecision(PromoteAction.HOLD, "not grounded: no replay-pass evidence")
    if high_risk:
        return PromoteDecision(PromoteAction.HUMAN_REVIEW, "high-risk skill always needs review")
    if breaker_open:
        return PromoteDecision(
            PromoteAction.HUMAN_REVIEW, "auto-promote circuit breaker is open (degraded)"
        )
    if not auto_promote_eligible:
        return PromoteDecision(
            PromoteAction.HUMAN_REVIEW, "grounding signal not strong enough for auto-promote"
        )
    if not within_rate_limit:
        return PromoteDecision(
            PromoteAction.HUMAN_REVIEW, "auto-promote rate limit exceeded; deferred to human"
        )
    return PromoteDecision(PromoteAction.AUTO_PROMOTE, "grounded + eligible within guardrails")


def should_auto_promote(decision: PromoteDecision) -> bool:
    """Convenience predicate for the worker's promote step."""
    return decision.action is PromoteAction.AUTO_PROMOTE
