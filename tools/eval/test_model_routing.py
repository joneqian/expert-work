"""Unit + integration tests for the J.11 model-routing eval — Stream J.13a."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from model_routing import (  # noqa: E402
    FallbackCase,
    FallbackProviderScript,
    ResolutionCase,
    evaluate_set,
    load_cases,
)


def test_load_cases_parses_resolution_and_fallback() -> None:
    """The shipped dataset has 12 resolution + 4 fallback cases."""
    cases = load_cases(_EVAL_DIR / "datasets" / "model_routing" / "m0_baseline.yaml")
    assert len(cases) == 16
    resolution = [c for c in cases if isinstance(c, ResolutionCase)]
    fallback = [c for c in cases if isinstance(c, FallbackCase)]
    assert len(resolution) == 12
    assert len(fallback) == 4


def test_load_cases_rejects_unknown_scenario(tmp_path: Path) -> None:
    """Unknown ``scenario`` values fail loud at load time."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("cases:\n  - id: bogus\n    scenario: typo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown scenario"):
        load_cases(bad)


@pytest.mark.asyncio
async def test_baseline_dataset_meets_threshold() -> None:
    """The shipped baseline cases all pass — every metric at 1.0."""
    cases = load_cases(_EVAL_DIR / "datasets" / "model_routing" / "m0_baseline.yaml")
    report = await evaluate_set(cases)
    assert report.status == "PASS"
    assert report.aggregate_score["pass_rate"] == 1.0
    assert report.sample_size == 16


@pytest.mark.asyncio
async def test_resolution_failure_drops_pass_rate() -> None:
    """A deliberately wrong expected_model_key produces a failure note."""
    case = ResolutionCase(
        case_id="r-bad",
        routing_rules=(("planning", "anthropic:opus-4-7"),),
        default_model_key="anthropic:haiku-4-5",
        step_class="planning",
        # Expected mismatches the actual resolution to exercise the
        # failure path.
        expected_model_key="openai:gpt-4o",
    )
    report = await evaluate_set([case])
    assert report.status == "FAIL"
    assert report.aggregate_score["pass_rate"] == 0.0
    assert "expected_model_key" in report.per_case[0].notes[0]


@pytest.mark.asyncio
async def test_fallback_client_error_short_circuits() -> None:
    """A 4xx on the primary re-raises without trying the next provider."""
    case = FallbackCase(
        case_id="f-bad",
        providers=(
            FallbackProviderScript(key="anthropic:primary", action="client_error"),
            FallbackProviderScript(key="openai:fallback", action="success"),
        ),
        expected_outcome="client_error",
    )
    report = await evaluate_set([case])
    assert report.status == "PASS"
    assert report.per_case[0].passed is True


@pytest.mark.asyncio
async def test_fallback_exhaustion_when_all_retryable() -> None:
    """Every retryable error → :class:`AllProvidersExhaustedError`."""
    case = FallbackCase(
        case_id="f-exhausted",
        providers=(
            FallbackProviderScript(key="p1", action="server_error"),
            FallbackProviderScript(key="p2", action="rate_limit"),
            FallbackProviderScript(key="p3", action="network_error"),
        ),
        expected_outcome="exhausted",
    )
    report = await evaluate_set([case])
    assert report.status == "PASS"
    assert report.per_case[0].passed is True


@pytest.mark.asyncio
async def test_fallback_primary_5xx_falls_through() -> None:
    """5xx on the primary lets the fallback's success carry the result."""
    case = FallbackCase(
        case_id="f-fallback-wins",
        providers=(
            FallbackProviderScript(key="anthropic:primary", action="server_error"),
            FallbackProviderScript(key="openai:fallback", action="success"),
        ),
        expected_outcome="success",
        expected_winner_key="openai:fallback",
    )
    report = await evaluate_set([case])
    assert report.status == "PASS"
    assert report.per_case[0].passed is True
