"""Unit tests for InMemoryStreamBridge + the make_stream_bridge factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from helix_agent.runtime.stream_bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    InMemoryStreamBridge,
    StreamEvent,
    make_stream_bridge,
)


async def _drain(
    it: AsyncIterator[StreamEvent],
    *,
    max_items: int = 100,
) -> list[StreamEvent]:
    """Helper: collect events from the iterator until END_SENTINEL or limit."""
    out: list[StreamEvent] = []
    async for ev in it:
        out.append(ev)
        if ev is END_SENTINEL or len(out) >= max_items:
            return out
    return out


@pytest.mark.asyncio
async def test_publish_subscribe_round_trip() -> None:
    bridge = InMemoryStreamBridge()
    run_id = uuid4()

    await bridge.publish(run_id, "metadata", {"agent": "demo"})
    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish_end(run_id)

    events = await _drain(bridge.subscribe(run_id))
    assert [e.event for e in events] == ["metadata", "updates", "__end__"]
    assert events[-1] is END_SENTINEL
    assert events[0].data == {"agent": "demo"}


@pytest.mark.asyncio
async def test_last_event_id_resumes_after_cursor() -> None:
    bridge = InMemoryStreamBridge()
    run_id = uuid4()

    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish(run_id, "updates", {"step": 2})
    await bridge.publish(run_id, "updates", {"step": 3})
    await bridge.publish_end(run_id)

    full = await _drain(bridge.subscribe(run_id))
    # Reconnect from id of the 1st event — should resume from event 2 onwards
    resume_id = full[0].id
    resumed = await _drain(bridge.subscribe(run_id, last_event_id=resume_id))
    assert [e.data for e in resumed if e is not END_SENTINEL] == [{"step": 2}, {"step": 3}]


@pytest.mark.asyncio
async def test_last_event_id_unknown_replays_from_earliest_retained() -> None:
    bridge = InMemoryStreamBridge()
    run_id = uuid4()

    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish(run_id, "updates", {"step": 2})
    await bridge.publish_end(run_id)

    replayed = await _drain(bridge.subscribe(run_id, last_event_id="nonexistent-cursor"))
    assert [e.data for e in replayed if e is not END_SENTINEL] == [{"step": 1}, {"step": 2}]


@pytest.mark.asyncio
async def test_heartbeat_on_idle() -> None:
    bridge = InMemoryStreamBridge()
    run_id = uuid4()
    # No events published, no publish_end. Subscriber must hit heartbeat.

    async def _collect() -> list[StreamEvent]:
        out: list[StreamEvent] = []
        async for ev in bridge.subscribe(run_id, heartbeat_interval=0.05):
            out.append(ev)
            if len(out) >= 2:
                return out
        return out

    events = await asyncio.wait_for(_collect(), timeout=1.0)
    assert all(e is HEARTBEAT_SENTINEL for e in events)


@pytest.mark.asyncio
async def test_buffer_overflow_drops_oldest() -> None:
    bridge = InMemoryStreamBridge(queue_maxsize=3)
    run_id = uuid4()

    for i in range(5):
        await bridge.publish(run_id, "updates", {"step": i})
    await bridge.publish_end(run_id)

    events = await _drain(bridge.subscribe(run_id))
    payload_steps = [e.data["step"] for e in events if e is not END_SENTINEL]
    assert payload_steps == [2, 3, 4]  # 0,1 dropped (maxsize=3)


@pytest.mark.asyncio
async def test_cleanup_releases_state() -> None:
    bridge = InMemoryStreamBridge()
    run_id = uuid4()
    await bridge.publish(run_id, "updates", {"step": 1})
    assert run_id in bridge._streams

    await bridge.cleanup(run_id)
    assert run_id not in bridge._streams
    assert run_id not in bridge._counters


@pytest.mark.asyncio
async def test_factory_memory_default() -> None:
    async with make_stream_bridge() as bridge:
        assert isinstance(bridge, InMemoryStreamBridge)


@pytest.mark.asyncio
async def test_factory_redis_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="redis"):
        async with make_stream_bridge("redis"):
            pass


@pytest.mark.asyncio
async def test_factory_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown stream_bridge backend"):
        async with make_stream_bridge("kafka"):  # type: ignore[arg-type]
            pass
