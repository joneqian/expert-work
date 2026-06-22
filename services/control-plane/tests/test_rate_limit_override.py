"""Unit tests for ``parse_rate_limit_override`` — Stream C.6 per-tenant override."""

from __future__ import annotations

import pytest

from control_plane.ratelimit import parse_rate_limit_override


def test_none_and_empty_return_none() -> None:
    assert parse_rate_limit_override(None) is None
    assert parse_rate_limit_override({}) is None


def test_requests_per_minute_maps_to_caps() -> None:
    ov = parse_rate_limit_override({"requests_per_minute": 600})
    assert ov is not None
    assert ov.requests_per_minute == 600
    assert ov.burst == 600  # default burst = rpm
    assert ov.capacity == 600
    assert ov.refill_per_sec == pytest.approx(10.0)  # 600/60


def test_explicit_burst() -> None:
    ov = parse_rate_limit_override({"requests_per_minute": 600, "burst": 1200})
    assert ov is not None
    assert ov.capacity == 1200
    assert ov.refill_per_sec == pytest.approx(10.0)


@pytest.mark.parametrize(
    "raw",
    [
        {"requests_per_minute": 0},  # must be >= 1
        {"requests_per_minute": -5},
        {"requests_per_minute": 1.5},  # not an int
        {"requests_per_minute": True},  # bool rejected
        {"requests_per_minute": "600"},  # string rejected
        {"burst": 100},  # missing requests_per_minute
        {"requests_per_minute": 600, "burst": 0},  # burst >= 1
        {"requests_per_minute": 600, "burst": "x"},
        {"requests_per_minute": 600, "rpm_typo": 1},  # unknown key
        {"requests_per_minute": 10_000_001},  # over max
    ],
)
def test_bad_shapes_raise(raw: dict) -> None:
    with pytest.raises(ValueError, match="rate_limit_override"):
        parse_rate_limit_override(raw)
