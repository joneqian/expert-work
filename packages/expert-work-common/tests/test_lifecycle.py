"""Unit tests for :mod:`expert_work.common.lifecycle`."""

from __future__ import annotations

import asyncio

import pytest

from expert_work.common.lifecycle import Lifecycle, ShutdownState

# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


def test_starts_in_starting_state() -> None:
    lc = Lifecycle()
    assert lc.state is ShutdownState.STARTING
    assert lc.drain_started_at is None
    assert lc.in_flight == 0


def test_mark_ready_transitions_to_running() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    assert lc.state is ShutdownState.RUNNING


def test_mark_ready_idempotent_in_running() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    lc.mark_ready()  # must not crash
    assert lc.state is ShutdownState.RUNNING


def test_mark_ready_after_draining_is_warned_not_raised() -> None:
    """A late mark_ready during shutdown should log but not crash; we
    don't want a programming bug here to mask the actual shutdown."""
    lc = Lifecycle()
    lc.mark_ready()
    lc._state = ShutdownState.DRAINING  # simulate mid-shutdown
    lc.mark_ready()  # must not raise
    assert lc.state is ShutdownState.DRAINING


# ---------------------------------------------------------------------------
# In-flight tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_track_in_flight_increments_and_decrements() -> None:
    lc = Lifecycle()
    async with lc.track_in_flight():
        assert lc.in_flight == 1
    assert lc.in_flight == 0


@pytest.mark.asyncio
async def test_track_in_flight_decrements_on_exception() -> None:
    """A handler that raises must still decrement the counter — otherwise
    drain never reaches zero (subsystems/28 § 6: 'in-flight leak').

    Use ``try`` / ``except`` rather than ``pytest.raises`` so CodeQL's
    flow analysis sees the post-raise assertion as reachable.
    """
    lc = Lifecycle()
    caught: RuntimeError | None = None
    try:
        async with lc.track_in_flight():
            assert lc.in_flight == 1
            raise RuntimeError("boom")
    except RuntimeError as exc:
        caught = exc

    assert caught is not None
    assert "boom" in str(caught)
    assert lc.in_flight == 0


@pytest.mark.asyncio
async def test_track_in_flight_nested() -> None:
    lc = Lifecycle()
    async with lc.track_in_flight():
        async with lc.track_in_flight():
            assert lc.in_flight == 2
        assert lc.in_flight == 1
    assert lc.in_flight == 0


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_shutdown_walks_state_machine() -> None:
    lc = Lifecycle()
    lc.mark_ready()

    states_seen: list[ShutdownState] = []

    async def _drain_hook() -> None:
        states_seen.append(lc.state)

    async def _cleanup_hook() -> None:
        states_seen.append(lc.state)

    lc.on_drain(_drain_hook)
    lc.on_cleanup(_cleanup_hook)

    await lc.graceful_shutdown()

    assert states_seen == [ShutdownState.DRAINING, ShutdownState.STOPPING]
    assert lc.state is ShutdownState.STOPPING
    assert lc.drain_started_at is not None


@pytest.mark.asyncio
async def test_graceful_shutdown_runs_drain_then_cleanup_in_order() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    order: list[str] = []

    async def _drain_a() -> None:
        order.append("drain_a")

    async def _drain_b() -> None:
        order.append("drain_b")

    async def _cleanup_a() -> None:
        order.append("cleanup_a")

    lc.on_drain(_drain_a)
    lc.on_drain(_drain_b)
    lc.on_cleanup(_cleanup_a)

    await lc.graceful_shutdown()
    assert order == ["drain_a", "drain_b", "cleanup_a"]


@pytest.mark.asyncio
async def test_graceful_shutdown_hook_failure_does_not_abort_rest() -> None:
    """One bad cleanup hook must not skip the others — losing one step is
    better than losing all of them."""
    lc = Lifecycle()
    lc.mark_ready()
    ran: list[str] = []

    async def _bad() -> None:
        raise RuntimeError("oops")

    async def _good() -> None:
        ran.append("good")

    lc.on_cleanup(_bad)
    lc.on_cleanup(_good)

    await lc.graceful_shutdown()
    assert ran == ["good"]
    assert lc.state is ShutdownState.STOPPING


@pytest.mark.asyncio
async def test_graceful_shutdown_waits_for_in_flight_zero() -> None:
    lc = Lifecycle(drain_timeout_s=1.0)
    lc.mark_ready()

    # Pretend a handler is in flight — shutdown must block.
    cm = lc.track_in_flight()
    await cm.__aenter__()
    try:
        # Start shutdown in a task; it should NOT complete until we release.
        shutdown_task = asyncio.create_task(lc.graceful_shutdown())
        await asyncio.sleep(0.05)
        assert not shutdown_task.done(), "shutdown should still be waiting"
        # Use ``==`` (not ``is``) so mypy does not narrow lc.state to a
        # Literal value here — the state mutates inside graceful_shutdown
        # before the next assertion.
        assert lc.state == ShutdownState.DRAINING
    finally:
        await cm.__aexit__(None, None, None)

    await asyncio.wait_for(shutdown_task, timeout=1.0)
    # mypy narrows ``lc.state`` to ``Literal[DRAINING]`` after the earlier
    # assertion; the state mutates inside ``graceful_shutdown`` between
    # there and here, so the next compare is fine at runtime even though
    # mypy can't see the mutation through the property accessor.
    assert lc.state == ShutdownState.STOPPING  # type: ignore[comparison-overlap]


@pytest.mark.asyncio
async def test_graceful_shutdown_force_progresses_on_drain_timeout() -> None:
    """If in-flight never reaches zero within drain_timeout_s, shutdown
    must still progress to cleanup (and log a warning)."""
    lc = Lifecycle(drain_timeout_s=0.05)  # tiny budget
    lc.mark_ready()

    # Hold one in-flight handler open past the drain budget.
    cm = lc.track_in_flight()
    await cm.__aenter__()
    try:
        await lc.graceful_shutdown()
        # Cleanup phase reached despite stuck in-flight.
        assert lc.state is ShutdownState.STOPPING
    finally:
        await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_second_shutdown_call_is_a_noop() -> None:
    lc = Lifecycle()
    lc.mark_ready()
    await lc.graceful_shutdown()
    # Re-entry from a second SIGTERM should not crash or rerun hooks.
    await lc.graceful_shutdown()
    assert lc.state is ShutdownState.STOPPING
