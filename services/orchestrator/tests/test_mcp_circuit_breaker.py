"""Unit tests for MCPCircuitBreaker — Capability Uplift Sprint #5
(Mini-ADR U-13).

State machine:

  closed  ── N consecutive failures ──→  open
   ▲                                       │
   │                                       │ window elapsed
   │     success in half_open              ▼
   └──────────────  half_open ◄────────────┘
                    │
                    │ failure in half_open
                    ▼
                  open  (window restarted)

Time is injected via ``now`` callable so the half-open probe doesn't
need real wall-clock sleeps.
"""

from __future__ import annotations

import pytest

from orchestrator.tools.mcp import (
    DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
    DEFAULT_CIRCUIT_WINDOW_S,
    MCPCircuitBreaker,
)


def _at(seconds: float) -> float:
    return seconds


def test_default_thresholds_match_design() -> None:
    """Mini-ADR U-13: 5 failures / 30 minute window."""
    assert DEFAULT_CIRCUIT_FAILURE_THRESHOLD == 5
    assert DEFAULT_CIRCUIT_WINDOW_S == 30 * 60


def test_closed_breaker_allows_calls() -> None:
    cb = MCPCircuitBreaker(server="github", now=lambda: _at(0))
    assert cb.state == "closed"
    assert cb.allow_call() is True


def test_failures_below_threshold_keep_breaker_closed() -> None:
    cb = MCPCircuitBreaker(server="github", failure_threshold=3, now=lambda: _at(0))
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"
    assert cb.allow_call() is True


def test_threshold_failures_open_breaker() -> None:
    cb = MCPCircuitBreaker(server="github", failure_threshold=3, now=lambda: _at(0))
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"


def test_open_breaker_rejects_calls() -> None:
    cb = MCPCircuitBreaker(server="github", failure_threshold=2, now=lambda: _at(0))
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow_call() is False


def test_breaker_half_opens_after_window_elapses() -> None:
    clock = {"t": 0.0}
    cb = MCPCircuitBreaker(
        server="github",
        failure_threshold=2,
        window_s=600.0,
        now=lambda: clock["t"],
    )
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    # Move past the window — next allow_call() probes half-open.
    clock["t"] = 700.0
    assert cb.allow_call() is True
    assert cb.state == "half_open"


def test_successful_call_in_half_open_closes_breaker() -> None:
    clock = {"t": 0.0}
    cb = MCPCircuitBreaker(
        server="github",
        failure_threshold=2,
        window_s=600.0,
        now=lambda: clock["t"],
    )
    cb.record_failure()
    cb.record_failure()
    clock["t"] = 700.0
    cb.allow_call()  # transitions to half_open
    cb.record_success()
    assert cb.state == "closed"


def test_failed_call_in_half_open_reopens_breaker() -> None:
    clock = {"t": 0.0}
    cb = MCPCircuitBreaker(
        server="github",
        failure_threshold=2,
        window_s=600.0,
        now=lambda: clock["t"],
    )
    cb.record_failure()
    cb.record_failure()
    clock["t"] = 700.0
    cb.allow_call()  # transitions to half_open
    cb.record_failure()
    assert cb.state == "open"
    # Reject probes again until another window elapses.
    assert cb.allow_call() is False
    clock["t"] = 1500.0  # 700 + 800 > 600 window from re-open
    assert cb.allow_call() is True


def test_successful_call_in_closed_resets_failure_count() -> None:
    cb = MCPCircuitBreaker(server="github", failure_threshold=3, now=lambda: _at(0))
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    # Two more failures should NOT trip — counter reset by the success.
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"


@pytest.mark.parametrize("threshold", [1, 5, 10])
def test_threshold_parameter_respected(threshold: int) -> None:
    cb = MCPCircuitBreaker(server="x", failure_threshold=threshold, now=lambda: _at(0))
    for _ in range(threshold - 1):
        cb.record_failure()
        assert cb.state == "closed"
    cb.record_failure()
    assert cb.state == "open"
