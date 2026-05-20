"""Unit tests for the blue/green deploy helper — Stream I.2.

Covers the pure functions (colour logic, upstream rendering, canary
parsing). The Docker-touching orchestration is exercised by the
``@pytest.mark.integration`` test in ``test_deploy_integration.py``.
"""

from __future__ import annotations

import pytest
from deploy import (
    UPSTREAM_CONF,
    CanaryAbortedError,
    deploy,
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


# --------------------------------------------------------------------------- K.K11 canary soak
#
# ``deploy.deploy`` itself touches Docker + the filesystem; the unit
# tests below intercept only the bits K.K11 added (soak_checker call
# + rollback wiring) by monkeypatching the subprocess / fs helpers on
# the imported module.


def _stub_deploy_io(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Replace every external effect deploy() makes with a recorder.

    Returns a dict mapping operation name → list of recorded arguments
    so tests can assert call order without touching Docker / nginx /
    the real upstream conf. String-based ``monkeypatch.setattr`` keeps
    the import style consistent — CodeQL flags mixing ``from deploy
    import …`` with ``import deploy`` on the same module.
    """
    calls: dict[str, list[object]] = {
        "compose": [],
        "wait_ready": [],
        "reload": [],
        "writes": [],
        "sleeps": [],
    }
    state = {"upstream": render_upstream("blue")}

    def _read_text(_self: object) -> str:
        return state["upstream"]

    def _fake_write(text: str) -> None:
        calls["writes"].append(text)
        state["upstream"] = text

    monkeypatch.setattr(UPSTREAM_CONF.__class__, "read_text", _read_text)
    monkeypatch.setattr("deploy.write_upstream", _fake_write)
    monkeypatch.setattr("deploy.reload_nginx", lambda: calls["reload"].append(None))
    monkeypatch.setattr("deploy.wait_ready", lambda colour, _t: calls["wait_ready"].append(colour))
    monkeypatch.setattr(
        "deploy._compose",
        lambda *args, tag=None: calls["compose"].append((args, tag)),
    )
    monkeypatch.setattr("deploy.time.sleep", lambda s: calls["sleeps"].append(s))
    return calls


def test_canary_soak_check_aborts_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stream K.K11 — a False soak check at the second canary step must
    restore upstream to 100% live and raise :class:`CanaryAbortedError`."""
    calls = _stub_deploy_io(monkeypatch)

    checked: list[int] = []

    def soak_checker(pct: int) -> bool:
        checked.append(pct)
        return pct < 30  # pass at 10, fail at 30

    with pytest.raises(CanaryAbortedError, match="canary aborted at 30%"):
        deploy(
            tag="vk11",
            canary=[10, 30, 50],
            canary_pause=0.0,
            drain_timeout=1,
            ready_timeout=1.0,
            soak_checker=soak_checker,
        )

    # The soak checker saw 10 then 30; we never reached 50.
    assert checked == [10, 30]
    # Final upstream is the original "100% blue" — restored on abort.
    assert calls["writes"][-1] == render_upstream("blue")
    # No drain / stop ran (deploy bailed before the final flip).
    assert not any("stop" in args for args, _ in calls["compose"])


def test_canary_soak_check_pass_all_completes_flip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A green soak at every step lets deploy finish: upstream ends at
    100% green and the old blue is stopped (deploy.py 's normal exit)."""
    calls = _stub_deploy_io(monkeypatch)

    deploy(
        tag="vk11",
        canary=[10, 50],
        canary_pause=0.0,
        drain_timeout=1,
        ready_timeout=1.0,
        soak_checker=lambda _pct: True,
    )

    # Final upstream is 100% green (no weights line).
    assert calls["writes"][-1] == render_upstream("green")
    # The drain stop ran against blue.
    stop_calls = [args for args, _ in calls["compose"] if "stop" in args]
    assert any("control-plane-blue" in a for a in stop_calls)


def test_canary_soak_check_raising_callback_counts_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A soak checker that raises is treated as a failed check (we don't
    want a flaky probe to gate the deploy on a silent exception)."""
    calls = _stub_deploy_io(monkeypatch)

    def _raising(_pct: int) -> bool:
        raise RuntimeError("probe broken")

    with pytest.raises(CanaryAbortedError):
        deploy(
            tag="vk11",
            canary=[10],
            canary_pause=0.0,
            drain_timeout=1,
            ready_timeout=1.0,
            soak_checker=_raising,
        )
    assert calls["writes"][-1] == render_upstream("blue")
