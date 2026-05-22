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
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.common.observability import helix_histogram
from helix_agent.persistence import ApprovalStore
from helix_agent.protocol import (
    ApprovalRecord,
    ApprovalRequest,
    AuditAction,
    AuditEntry,
    AuditResult,
)
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.cancellation import (
    CANCELLATION_TOKEN_KEY,
    CancellationToken,
    RunCancelledError,
)
from helix_agent.runtime.runs import RunManager, RunRecord, RunStatus
from helix_agent.runtime.stream_bridge import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    StreamBridge,
)
from orchestrator.errors import MaxStepsExceededError
from orchestrator.trajectory import (
    TrajectoryOutcome,
    TrajectoryRecord,
    TrajectoryRecorder,
)

logger = logging.getLogger(__name__)


# Stream K.K10 — Session TTFT (Time-To-First-Token). Measured from the
# moment ``run_agent`` flips the run to ``RUNNING`` to the first real
# ``updates`` chunk published to the bridge. The ``metadata`` event we
# publish synchronously beforehand isn't counted — TTFT tracks how long
# the user waits for actual agent work to start. SLO #3 (slo.md):
# P95 < 1.5s @ 30d.
_session_ttft_seconds = helix_histogram(
    "helix_session_ttft_seconds",
    "Seconds from RUNNING to first agent update chunk.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# Stream K.K10 — Durable resume duration. Measured at the same point as
# TTFT, but only emitted when the run resumed an existing checkpointed
# thread. The control-plane decides ``is_resume`` from the thread's
# pre-existing state (see ``runs.py``) and passes it via the
# ``RunRecord``. SLO #5 reformulated to seconds-of-resume + a success
# counter (resume failures are run failures → already counted).
_durable_resume_seconds = helix_histogram(
    "helix_durable_resume_seconds",
    "Seconds from RUNNING to first chunk on a resumed (non-empty checkpoint) run.",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# Stream M Gate — Session end-to-end duration. Measured from just before
# ``set_status(RUNNING)`` to the run's terminal status (the ``finally``
# block, so every exit path emits exactly once). Labelled by ``outcome``
# so Gate dashboards can query successful-run P95 separately from
# error / max_steps / cancelled tails — Stream M Exit Criteria
# (STREAM-M-DESIGN § 2.1) targets the user-facing successful-run latency.
_session_duration_seconds = helix_histogram(
    "helix_session_duration_seconds",
    "Seconds from RUNNING to terminal status; labelled by run outcome.",
    ("outcome",),
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

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

    async def aget_state(self, config: RunnableConfig) -> Any:
        """Return the run's final checkpointed state.

        Stream L.L7 — :func:`run_agent` calls this on terminal paths to
        fetch the conversation messages for trajectory recording.
        ``langgraph.graph.state.CompiledStateGraph.aget_state`` returns
        a ``StateSnapshot`` whose ``values`` dict carries
        :class:`AgentState`. We accept ``Any`` here because the
        Protocol is intentionally minimal — the snapshot's exact shape
        is LangGraph-internal.
        """


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
    audit_logger: AuditLogger | None = None,
    stream_mode: str = DEFAULT_STREAM_MODE,
    trajectory_recorder: TrajectoryRecorder | None = None,
    approval_store: ApprovalStore | None = None,
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

    When ``audit_logger`` is supplied, one run-lifecycle row lands at
    run end — ``run:completed`` on a normal / interrupted finish,
    ``run:failed`` on an exception. The defensive
    :class:`asyncio.CancelledError` path is *not* audited: awaiting the
    logger during loop teardown is unreliable. Audit-write failures are
    logged and swallowed — they never fail the run.
    """
    run_id = record.run_id
    # Bind a cancellation token to the run's abort_event and thread it
    # to graph nodes via config["configurable"] (E.15). RunManager.cancel
    # sets abort_event → the token reports cancelled → nodes surface
    # RunCancelledError at their next checkpoint.
    token = CancellationToken.from_event(record.abort_event)
    effective_config: RunnableConfig = {
        **config,
        "configurable": {
            **(config.get("configurable") or {}),
            CANCELLATION_TOKEN_KEY: token,
        },
    }
    # Stream M Gate — session E2E duration. Started before ``set_status``
    # so the ``finally`` block always sees a valid clock; ``session_outcome``
    # defaults to ``"error"`` for the worst-case path (a crash before any
    # branch sets it). Each terminal branch below updates ``session_outcome``
    # before its ``await`` chain so ``_session_duration_seconds`` carries
    # the correct label even if a later step in that branch raises.
    session_started = time.monotonic()
    session_outcome = "error"
    try:
        await run_manager.set_status(run_id, RunStatus.RUNNING)
        await bridge.publish(
            run_id,
            "metadata",
            {"run_id": str(run_id), "thread_id": str(record.thread_id)},
        )

        # Stream K.K10 — start the TTFT / durable-resume timer at RUNNING.
        # The metadata frame above is server-synthesised, not LLM output,
        # so we measure from this point to the first ``updates`` chunk.
        ttft_started = time.monotonic()
        first_chunk_seen = False
        async for chunk in graph.astream(graph_input, effective_config, stream_mode=stream_mode):
            if record.abort_event.is_set():
                logger.info("run_agent.abort_requested run_id=%s", run_id)
                break
            if not first_chunk_seen:
                ttft = time.monotonic() - ttft_started
                _session_ttft_seconds.observe(ttft)
                if getattr(record, "is_resume", False):
                    _durable_resume_seconds.observe(ttft)
                first_chunk_seen = True
            await bridge.publish(run_id, stream_mode, _to_jsonable(chunk))

        # Stream J.8 (Mini-ADR J-24) — a run that streamed to its natural
        # end with ``pending_approval`` set did not finish: it paused at
        # an approval gate (RunStatus.PAUSED). The checkpoint persists so
        # ``POST .../resume`` can re-invoke. A paused run emits no
        # RUN_COMPLETED audit + no trajectory — both belong to the *true*
        # run end after a resume. A failing ``aget_state`` degrades to
        # "not paused" (same graceful-degradation contract as the
        # trajectory recorder) rather than failing the run.
        pending_request: ApprovalRequest | None = None
        if not record.abort_event.is_set():
            try:
                snapshot = await graph.aget_state(effective_config)
                raw_pending = snapshot.values.get("pending_approval")
                if raw_pending is not None:
                    pending_request = (
                        raw_pending
                        if isinstance(raw_pending, ApprovalRequest)
                        else ApprovalRequest.model_validate(raw_pending)
                    )
            except Exception:
                logger.warning("run_agent.pause_check_failed run_id=%s", run_id, exc_info=True)

        if record.abort_event.is_set():
            final = RunStatus.INTERRUPTED
        elif pending_request is not None:
            final = RunStatus.PAUSED
        else:
            final = RunStatus.SUCCESS
        session_outcome = {
            RunStatus.INTERRUPTED: "interrupted",
            RunStatus.PAUSED: "paused",
            RunStatus.SUCCESS: "success",
        }[final]
        await run_manager.set_status(run_id, final)
        if final is RunStatus.PAUSED and pending_request is not None:
            # Register the paused run in the durable ``agent_approval``
            # table + emit APPROVAL_REQUESTED. The table — not the
            # in-memory RunManager — is what the resume endpoint, the
            # GET surface, and the 24h timeout job all consult.
            await _register_pending_approval(
                approval_store=approval_store,
                audit_logger=audit_logger,
                record=record,
                request=pending_request,
            )
        if final is not RunStatus.PAUSED:
            await _emit_run_end_audit(
                audit_logger,
                record,
                action=AuditAction.RUN_COMPLETED,
                result=AuditResult.SUCCESS,
                reason=None,
                status="interrupted" if final is RunStatus.INTERRUPTED else "success",
            )
            # Stream L.L7 — record the trajectory for the J.13 eval gate.
            # Fire-and-forget; failures are swallowed inside the recorder.
            _dispatch_trajectory(
                trajectory_recorder,
                graph,
                effective_config,
                record,
                outcome="cancelled" if final is RunStatus.INTERRUPTED else "success",
            )

    except RunCancelledError:
        # A node surfaced cooperative cancellation mid-step (E.15) — a
        # normal interrupted finish, not a failure.
        session_outcome = "interrupted"
        await run_manager.set_status(run_id, RunStatus.INTERRUPTED)
        logger.info("run_agent.cancelled_cooperatively run_id=%s", run_id)
        await _emit_run_end_audit(
            audit_logger,
            record,
            action=AuditAction.RUN_COMPLETED,
            result=AuditResult.SUCCESS,
            reason=None,
            status="interrupted",
        )
        _dispatch_trajectory(
            trajectory_recorder, graph, effective_config, record, outcome="cancelled"
        )
    except asyncio.CancelledError:
        # Task-level cancellation (event-loop shutdown / explicit
        # task.cancel()). The cooperative abort_event path above is the
        # normal cancel route; this is the defensive backstop. We do
        # NOT dispatch the trajectory here — awaiting any helper during
        # loop teardown is unreliable (same reason
        # ``_emit_run_end_audit`` is skipped on this path).
        session_outcome = "cancelled"
        await run_manager.set_status(run_id, RunStatus.INTERRUPTED)
        logger.info("run_agent.cancelled run_id=%s", run_id)
        raise
    except MaxStepsExceededError as exc:
        # Distinct from a generic failure — the agent hit its iteration
        # budget. The J.13 eval gate filters this bucket separately
        # because "budget exhausted" is a tunable trade-off, not a
        # provider / code failure.
        session_outcome = "max_steps"
        await run_manager.set_status(run_id, RunStatus.ERROR, error=str(exc))
        logger.warning(
            "run_agent.max_steps_exceeded run_id=%s step_count=%d max_steps=%d",
            run_id,
            exc.step_count,
            exc.max_steps,
        )
        await bridge.publish(
            run_id,
            "error",
            {"message": str(exc), "name": type(exc).__name__},
        )
        await _emit_run_end_audit(
            audit_logger,
            record,
            action=AuditAction.RUN_FAILED,
            result=AuditResult.ERROR,
            reason=str(exc),
            status="error",
        )
        _dispatch_trajectory(
            trajectory_recorder, graph, effective_config, record, outcome="max_steps"
        )
    except Exception as exc:
        session_outcome = "error"
        await run_manager.set_status(run_id, RunStatus.ERROR, error=str(exc))
        logger.exception("run_agent.failed run_id=%s", run_id)
        await bridge.publish(
            run_id,
            "error",
            {"message": str(exc), "name": type(exc).__name__},
        )
        await _emit_run_end_audit(
            audit_logger,
            record,
            action=AuditAction.RUN_FAILED,
            result=AuditResult.ERROR,
            reason=str(exc),
            status="error",
        )
        _dispatch_trajectory(trajectory_recorder, graph, effective_config, record, outcome="failed")
    finally:
        # Stream M Gate — emit on every terminal path. Always synchronous
        # so the ``asyncio.CancelledError`` teardown path (no await) still
        # counts the session in the histogram.
        _session_duration_seconds.labels(outcome=session_outcome).observe(
            time.monotonic() - session_started
        )
        await bridge.publish_end(run_id)
        # Fire-and-forget cleanup; keep a reference so the task isn't
        # garbage-collected mid-flight.
        cleanup_task = asyncio.create_task(bridge.cleanup(run_id, delay=_CLEANUP_DELAY_S))
        _BACKGROUND_CLEANUP_TASKS.add(cleanup_task)
        cleanup_task.add_done_callback(_BACKGROUND_CLEANUP_TASKS.discard)


#: Strong refs to in-flight cleanup tasks — without this the event loop
#: may garbage-collect a bare ``create_task`` result before it runs.
_BACKGROUND_CLEANUP_TASKS: set[asyncio.Task[None]] = set()

#: Strong refs to in-flight trajectory dispatch tasks (Stream L.L7) —
#: same garbage-collection guard as ``_BACKGROUND_CLEANUP_TASKS``.
_BACKGROUND_TRAJECTORY_TASKS: set[asyncio.Task[None]] = set()

#: Wall-clock cap on trajectory-record dispatch. The recorder swallows
#: its own errors; this deadline guards against an unrecoverably-slow
#: ObjectStore put dragging the run's terminal path or piling up tasks.
_TRAJECTORY_DISPATCH_TIMEOUT_S: float = 5.0


def _dispatch_trajectory(
    recorder: TrajectoryRecorder | None,
    graph: StreamableGraph,
    config: RunnableConfig,
    record: RunRecord,
    *,
    outcome: TrajectoryOutcome,
) -> None:
    """Stream L.L7 — schedule a fire-and-forget trajectory write.

    Returns immediately. The actual ObjectStore I/O happens in a
    background task with a hard timeout so a slow / broken store
    cannot stall the run's terminal path (TraJectoryRecorder.record
    swallows its own errors; this deadline is the outer guard).
    ``recorder=None`` is a no-op — manifest opt-out or recorder not
    configured in this deployment.
    """
    if recorder is None:
        return
    task = asyncio.create_task(
        _record_trajectory_safe(recorder, graph, config, record, outcome=outcome)
    )
    _BACKGROUND_TRAJECTORY_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TRAJECTORY_TASKS.discard)


async def _record_trajectory_safe(
    recorder: TrajectoryRecorder,
    graph: StreamableGraph,
    config: RunnableConfig,
    record: RunRecord,
    *,
    outcome: TrajectoryOutcome,
) -> None:
    """Background body for :func:`_dispatch_trajectory`."""
    try:
        async with asyncio.timeout(_TRAJECTORY_DISPATCH_TIMEOUT_S):
            messages = await _fetch_final_messages(graph, config)
            tenant_id = _tenant_id_from_config(config) or record.tenant_id
            user_id = _user_id_from_config(config)
            await recorder.record(
                TrajectoryRecord(
                    thread_id=record.thread_id,
                    tenant_id=tenant_id,
                    outcome=outcome,
                    messages=messages,
                    user_id=user_id,
                    run_id=record.run_id,
                    finished_at=datetime.now(UTC),
                )
            )
    except TimeoutError:
        logger.warning(
            "run_agent.trajectory_dispatch_timeout run_id=%s outcome=%s",
            record.run_id,
            outcome,
        )
    except Exception:
        # The recorder catches its own errors; reaching here means the
        # state fetch itself failed. Best-effort by design.
        logger.exception(
            "run_agent.trajectory_dispatch_failed run_id=%s outcome=%s",
            record.run_id,
            outcome,
        )


async def _fetch_final_messages(
    graph: StreamableGraph, config: RunnableConfig
) -> Sequence[BaseMessage]:
    """Best-effort fetch of the run's terminal ``messages`` list.

    Tries ``graph.aget_state(config)`` if the graph exposes it (the
    real ``CompiledStateGraph`` does; test stubs may not). On any
    failure the returned list is empty — the recorder will still
    write the envelope, just without conversation content. That's
    preferable to crashing the dispatch task.
    """
    aget_state = getattr(graph, "aget_state", None)
    if not callable(aget_state):
        return []
    try:
        snapshot = await aget_state(config)
    except Exception:
        logger.exception("run_agent.trajectory_state_fetch_failed")
        return []
    values = getattr(snapshot, "values", None)
    if not isinstance(values, Mapping):
        return []
    raw = values.get("messages")
    if not isinstance(raw, Sequence):
        return []
    out: list[BaseMessage] = [m for m in raw if isinstance(m, BaseMessage)]
    return out


def _tenant_id_from_config(config: RunnableConfig) -> UUID | None:
    configurable = config.get("configurable") or {}
    return _maybe_uuid(configurable.get("tenant_id"))


def _user_id_from_config(config: RunnableConfig) -> UUID | None:
    configurable = config.get("configurable") or {}
    return _maybe_uuid(configurable.get("user_id"))


def _maybe_uuid(raw: object) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            return None
    return None


async def _emit_run_end_audit(
    audit_logger: AuditLogger | None,
    record: RunRecord,
    *,
    action: AuditAction,
    result: AuditResult,
    reason: str | None,
    status: str,
) -> None:
    """Write one run-lifecycle audit row.

    The orchestrator worker is the actor (``actor_type="system"``); the
    row is keyed to the run's session so it sits alongside the
    ``session:write`` row the control-plane emits at run start. An
    audit-write failure is logged and swallowed — it must never fail
    an otherwise-finished run.
    """
    if audit_logger is None:
        return
    try:
        await audit_logger.write(
            AuditEntry(
                tenant_id=record.tenant_id,
                actor_type="system",
                actor_id="orchestrator",
                action=action,
                resource_type="session",
                resource_id=str(record.thread_id),
                result=result,
                reason=reason,
                details={"run_id": str(record.run_id), "status": status},
            )
        )
    except Exception:
        logger.exception("run_agent.audit_failed run_id=%s", record.run_id)


async def _register_pending_approval(
    *,
    approval_store: ApprovalStore | None,
    audit_logger: AuditLogger | None,
    record: RunRecord,
    request: ApprovalRequest,
) -> None:
    """Persist a paused run's ``agent_approval`` row + emit APPROVAL_REQUESTED.

    Stream J.8 (Mini-ADR J-24). The durable row — not the in-memory
    RunManager — is what survives a control-plane restart so the resume
    endpoint / GET surface / 24h timeout job can all find the paused
    run. A store / audit failure is logged and swallowed: the run
    already paused cleanly with its checkpoint intact; a missing
    registry row degrades to "resume-by-checkpoint only", never a
    crash.
    """
    if approval_store is not None:
        try:
            await approval_store.create(
                ApprovalRecord(
                    id=uuid4(),
                    tenant_id=record.tenant_id,
                    user_id=None,
                    run_id=record.run_id,
                    thread_id=record.thread_id,
                    request_id=request.request_id,
                    node=request.node,
                    reason_kind=request.reason_kind,
                    action_summary=request.action_summary,
                    proposed_args=request.proposed_args,
                    requested_at=request.requested_at,
                    timeout_at=request.timeout_at,
                )
            )
        except Exception:
            logger.exception("run_agent.approval_register_failed run_id=%s", record.run_id)
    if audit_logger is not None:
        try:
            await audit_logger.write(
                AuditEntry(
                    tenant_id=record.tenant_id,
                    actor_type="system",
                    actor_id="orchestrator",
                    action=AuditAction.APPROVAL_REQUESTED,
                    resource_type="approval",
                    resource_id=str(record.run_id),
                    result=AuditResult.SUCCESS,
                    reason=None,
                    details={
                        "run_id": str(record.run_id),
                        "thread_id": str(record.thread_id),
                        "node": request.node,
                        "reason_kind": request.reason_kind,
                        "action_summary": request.action_summary,
                    },
                )
            )
        except Exception:
            logger.exception("run_agent.approval_audit_failed run_id=%s", record.run_id)


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
