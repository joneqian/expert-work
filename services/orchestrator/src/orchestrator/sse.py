"""SSE streaming — background run worker + SSE consumer (Stream E.14).

In-process monolith model (STREAM-E-DESIGN § 2.6, corrected against
deer-flow's ``gateway`` + ``runtime`` co-location). There is **no**
separate orchestrator service: the control-plane FastAPI app imports
this module, runs graphs as background ``asyncio.Task``s, and streams
their events to clients over SSE.

Two halves, decoupled by :class:`StreamBridge`:

- :func:`run_agent` — the **producer**. A background task that drives a
  compiled LangGraph graph via ``graph.astream(...)`` and publishes
  each chunk to the bridge. Cooperative cancellation: it polls
  ``record.abort_event`` between chunks (set by
  :meth:`RunManager.cancel`). Always publishes a terminal ``end`` via
  :meth:`StreamBridge.publish_end`, even on error / cancel, so no
  consumer hangs.
- :func:`sse_consumer` — the **consumer**. An async generator that
  subscribes to the bridge and yields SSE wire frames. Its ``finally``
  block implements ``on_disconnect``: when the client disconnects and
  the run's mode is :data:`DisconnectMode.CANCEL`, the run is
  cancelled.

The worker is **graph-injected** — it receives an already-compiled
graph. Assembling a graph from an agent manifest (the "agent factory")
is a separate concern out of E.14 scope.

Backpressure is the :class:`StreamBridge`'s bounded-buffer drop-oldest
(Mini-ADR E-8, M0): a consumer that falls behind resumes from the
earliest retained event. There is no cancel-on-full in M0.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.runtime.runs import RunManager, RunRecord, RunStatus
from helix_agent.runtime.stream_bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    StreamBridge,
)

logger = logging.getLogger(__name__)

#: Default LangGraph stream mode. ``"updates"`` yields ``{node: writes}``
#: per step — the natural granularity for a ReAct SSE stream (one event
#: per agent / tools node completion).
DEFAULT_STREAM_MODE = "updates"

#: Seconds to keep a finished run's events in the bridge before cleanup,
#: giving late / reconnecting consumers time to drain.
_CLEANUP_DELAY_S = 60.0


class StreamableGraph(Protocol):
    """The slice of a compiled LangGraph graph :func:`run_agent` needs.

    ``langgraph.graph.state.CompiledStateGraph`` satisfies this
    structurally; tests inject a scripted async-iterator stub.
    """

    def astream(
        self,
        input: Any,
        config: RunnableConfig | None = ...,
        *,
        stream_mode: str | list[str] | None = ...,
    ) -> AsyncIterator[Any]:
        """Yield streaming chunks for one graph execution."""


# ---------------------------------------------------------------------------
# Producer — background run worker
# ---------------------------------------------------------------------------


async def run_agent(
    *,
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    graph: StreamableGraph,
    graph_input: Any,
    config: RunnableConfig,
    stream_mode: str = DEFAULT_STREAM_MODE,
) -> None:
    """Drive ``graph`` to completion, publishing events to ``bridge``.

    Intended to run as a background :class:`asyncio.Task`. Lifecycle:

    1. Mark the run ``RUNNING``; publish a ``metadata`` event.
    2. Stream ``graph.astream(...)`` chunks → ``bridge.publish``. Between
       chunks, poll ``record.abort_event`` — set means cooperative
       cancel, so stop early.
    3. Final status: ``INTERRUPTED`` if aborted, else ``SUCCESS``.
    4. On exception: status ``ERROR`` + publish an ``error`` event.
    5. **Always** (``finally``): ``publish_end`` + schedule bridge
       cleanup. A terminal ``end`` reaches every consumer no matter how
       the run finished.
    """
    run_id = record.run_id
    try:
        await run_manager.set_status(run_id, RunStatus.RUNNING)
        await bridge.publish(
            run_id,
            "metadata",
            {"run_id": str(run_id), "thread_id": str(record.thread_id)},
        )

        async for chunk in graph.astream(graph_input, config, stream_mode=stream_mode):
            if record.abort_event.is_set():
                logger.info("run_agent.abort_requested run_id=%s", run_id)
                break
            await bridge.publish(run_id, stream_mode, _to_jsonable(chunk))

        final = RunStatus.INTERRUPTED if record.abort_event.is_set() else RunStatus.SUCCESS
        await run_manager.set_status(run_id, final)

    except asyncio.CancelledError:
        # Task-level cancellation (event-loop shutdown / explicit
        # task.cancel()). The cooperative abort_event path above is the
        # normal cancel route; this is the defensive backstop.
        await run_manager.set_status(run_id, RunStatus.INTERRUPTED)
        logger.info("run_agent.cancelled run_id=%s", run_id)
        raise
    except Exception as exc:
        await run_manager.set_status(run_id, RunStatus.ERROR)
        logger.exception("run_agent.failed run_id=%s", run_id)
        await bridge.publish(
            run_id,
            "error",
            {"message": str(exc), "name": type(exc).__name__},
        )
    finally:
        await bridge.publish_end(run_id)
        # Fire-and-forget cleanup; keep a reference so the task isn't
        # garbage-collected mid-flight.
        cleanup_task = asyncio.create_task(bridge.cleanup(run_id, delay=_CLEANUP_DELAY_S))
        _BACKGROUND_CLEANUP_TASKS.add(cleanup_task)
        cleanup_task.add_done_callback(_BACKGROUND_CLEANUP_TASKS.discard)


#: Strong refs to in-flight cleanup tasks — without this the event loop
#: may garbage-collect a bare ``create_task`` result before it runs.
_BACKGROUND_CLEANUP_TASKS: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Consumer — SSE generator
# ---------------------------------------------------------------------------


async def sse_consumer(
    *,
    bridge: StreamBridge,
    record: RunRecord,
    run_manager: RunManager,
    is_disconnected: Callable[[], Awaitable[bool]],
    last_event_id: str | None = None,
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[bytes]:
    """Yield SSE wire frames for ``record``'s run.

    Subscribes to ``bridge`` and translates each :class:`StreamEvent`
    into an SSE frame. :data:`HEARTBEAT_SENTINEL` becomes an SSE comment
    (``: heartbeat``); :data:`END_SENTINEL` becomes a final ``end``
    event and terminates the generator.

    ``is_disconnected`` is an injected coroutine (FastAPI's
    ``request.is_disconnected`` in production) — checked before each
    yield so a vanished client stops the stream promptly.

    The ``finally`` block enforces ``on_disconnect``: if the run is
    still in flight and its mode is :data:`DisconnectMode.CANCEL`, the
    run is cancelled. A run that already finished is left untouched.
    """
    try:
        async for entry in bridge.subscribe(
            record.run_id,
            last_event_id=last_event_id,
            heartbeat_interval=heartbeat_interval,
        ):
            if await is_disconnected():
                logger.info("sse_consumer.client_disconnected run_id=%s", record.run_id)
                break

            if entry is HEARTBEAT_SENTINEL:
                yield b": heartbeat\n\n"
                continue

            if entry is END_SENTINEL:
                yield format_sse("end", None)
                return

            yield format_sse(entry.event, entry.data, event_id=entry.id or None)
    finally:
        from helix_agent.runtime.runs import DisconnectMode

        if (
            record.status in (RunStatus.PENDING, RunStatus.RUNNING)
            and record.on_disconnect is DisconnectMode.CANCEL
        ):
            await run_manager.cancel(record.run_id)


def format_sse(event: str, data: Any, *, event_id: str | None = None) -> bytes:
    """Render one SSE event in wire format (``id:`` / ``event:`` / ``data:``).

    ``data`` is JSON-encoded; ``None`` renders as ``data: null``. Frames
    end with the blank line the SSE spec requires as a record separator.
    """
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Chunk serialisation
# ---------------------------------------------------------------------------


def _to_jsonable(value: Any) -> Any:
    """Recursively convert a LangGraph stream chunk to JSON-safe data.

    LangGraph ``updates`` chunks are ``{node: {channel: value}}`` dicts
    whose values include :class:`BaseMessage` instances, UUIDs, and
    datetimes — none JSON-serialisable as-is. Conversions:

    - :class:`BaseMessage` → ``msg.model_dump()`` (LangChain's canonical
      dict form: type / content / id / tool_calls / ...).
    - :class:`UUID` → ``str``; :class:`datetime` → ISO-8601 string.
    - ``Mapping`` / ``Sequence`` → recurse element-wise.
    - Anything else with no obvious encoding → ``str(value)`` fallback
      (a stream event must never crash the run on a stray type).
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, BaseMessage):
        return _to_jsonable(value.model_dump())
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, Sequence | set | frozenset):
        return [_to_jsonable(v) for v in value]
    return str(value)
