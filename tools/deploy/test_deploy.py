"""Unit tests for the blue/green deploy helper — Stream I.2.

Covers the pure functions (colour logic, upstream rendering, canary
parsing). The Docker-touching orchestration is exercised by the
``@pytest.mark.integration`` test in ``test_deploy_integration.py``.
"""

from __future__ import annotations

import pytest
from deploy import (
    UPSTREAM_CONF,
    other_color,
    parse_canary_steps,
    parse_live_color,
    render_upstream,
)

# --------------------------------------------------------------------------- other_color


def test_other_color_swaps() -> None:
    assert other_color("blue") == "green"
    assert other_color("green") == "blue"


def test_other_color_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown colour"):
        other_color("red")


# --------------------------------------------------------------------------- parse_live_color


def test_parse_live_color_single_server() -> None:
    conf = "upstream control_plane_upstream {\n    server control-plane-green:8000;\n}\n"
    assert parse_live_color(conf) == "green"


def test_parse_live_color_weighted_returns_majority() -> None:
    conf = (
        "upstream control_plane_upstream {\n"
        "    server control-plane-blue:8000 weight=90;\n"
        "    server control-plane-green:8000 weight=10;\n"
        "}\n"
    )
    assert parse_live_color(conf) == "blue"


def test_parse_live_color_empty_raises() -> None:
    with pytest.raises(ValueError, match="no control-plane server line"):
        parse_live_color("upstream control_plane_upstream {\n}\n")


def test_parse_live_color_on_committed_file() -> None:
    """The checked-in upstream conf routes to blue by default."""
    assert parse_live_color(UPSTREAM_CONF.read_text()) == "blue"


# --------------------------------------------------------------------------- render_upstream


def test_render_upstream_single_colour() -> None:
    rendered = render_upstream("green")
    assert "server control-plane-green:8000;" in rendered
    assert "weight=" not in rendered
    assert "keepalive 32;" in rendered


def test_render_upstream_canary_weights() -> None:
    rendered = render_upstream("blue", canary_to_idle=10)
    assert "server control-plane-blue:8000 weight=90;" in rendered
    assert "server control-plane-green:8000 weight=10;" in rendered


def test_render_upstream_canary_out_of_range_raises() -> None:
    for bad in (0, 100, -5, 130):
        with pytest.raises(ValueError, match="canary_to_idle"):
            render_upstream("blue", canary_to_idle=bad)


def test_render_upstream_rejects_unknown_colour() -> None:
    with pytest.raises(ValueError, match="unknown colour"):
        render_upstream("red")


def test_render_then_parse_round_trips() -> None:
    for colour in ("blue", "green"):
        assert parse_live_color(render_upstream(colour)) == colour
    # A canary still reports the majority colour as live.
    assert parse_live_color(render_upstream("green", canary_to_idle=20)) == "green"


def test_render_matches_committed_default() -> None:
    """deploy.py's render must reproduce the checked-in file verbatim —
    a no-op deploy then leaves the upstream conf unchanged."""
    assert render_upstream("blue") == UPSTREAM_CONF.read_text()


# --------------------------------------------------------------------------- parse_canary_steps


def test_parse_canary_steps_parses_list() -> None:
    assert parse_canary_steps("10,50") == [10, 50]


def test_parse_canary_steps_empty() -> None:
    assert parse_canary_steps(None) == []
    assert parse_canary_steps("") == []


def test_parse_canary_steps_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        parse_canary_steps("10,100")
