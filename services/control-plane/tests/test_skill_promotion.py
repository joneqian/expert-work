"""Tests for the SE-7a auto-promote policy (the governance gate)."""

from __future__ import annotations

from control_plane.skill_promotion import PromoteAction, decide_promotion


def _decide(**over: object) -> object:
    base: dict[str, object] = {
        "grounded": True,
        "auto_promote_eligible": True,
        "high_risk": False,
        "breaker_open": False,
        "within_rate_limit": True,
    }
    base.update(over)
    return decide_promotion(**base)  # type: ignore[arg-type]


def test_eligible_grounded_auto_promotes() -> None:
    d = _decide()
    assert d.action is PromoteAction.AUTO_PROMOTE


def test_not_grounded_holds() -> None:
    d = _decide(grounded=False)
    assert d.action is PromoteAction.HOLD


def test_high_risk_always_human() -> None:
    d = _decide(high_risk=True)
    assert d.action is PromoteAction.HUMAN_REVIEW
    assert "high-risk" in d.reason


def test_not_eligible_goes_to_human() -> None:
    d = _decide(auto_promote_eligible=False)
    assert d.action is PromoteAction.HUMAN_REVIEW


def test_breaker_open_degrades_to_human() -> None:
    d = _decide(breaker_open=True)
    assert d.action is PromoteAction.HUMAN_REVIEW
    assert "circuit" in d.reason


def test_rate_limited_defers_to_human() -> None:
    d = _decide(within_rate_limit=False)
    assert d.action is PromoteAction.HUMAN_REVIEW
    assert "rate" in d.reason


def test_breaker_takes_precedence_over_eligible() -> None:
    # Even a perfectly eligible candidate is held for human when the breaker trips.
    d = _decide(breaker_open=True, auto_promote_eligible=True)
    assert d.action is PromoteAction.HUMAN_REVIEW


def test_high_risk_precedence_over_breaker_message() -> None:
    # High-risk is the hardest gate; its reason wins.
    d = _decide(high_risk=True, breaker_open=True)
    assert d.action is PromoteAction.HUMAN_REVIEW
    assert "high-risk" in d.reason


def test_should_auto_promote_helper() -> None:
    from control_plane.skill_promotion import should_auto_promote

    assert should_auto_promote(_decide()) is True
    assert should_auto_promote(_decide(high_risk=True)) is False
