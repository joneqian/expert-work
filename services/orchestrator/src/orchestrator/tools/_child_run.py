"""Shared child-agent run core — Stream J.4 (sub-agent) + 1.3 (dynamic worker).

Both :class:`~orchestrator.tools.subagent.SubAgentTool` (static ``agent_ref``
delegation) and :class:`~orchestrator.tools.spawn_worker.SpawnWorkerTool`
(dynamic ephemeral worker) build a child :class:`BuiltAgent` and then run it
to completion *the same way*:

* a fresh ``thread_id`` / ``run_id`` seeded with the delegated ``task``,
* the parent's :class:`CancellationToken` + ``deadline_at`` shared so a
  parent cancel / global-deadline reaches every child node,
* a fire-and-forget L7 trajectory write (Mini-ADR J-21) so J.13 eval can
  replay every node of the delegation tree,
* the child's final answer returned as a :class:`ToolResult` carrying a
  :class:`SubAgentInvocation` in ``state_updates``.

The two tools differ only in **how they obtain the child** and the
``label`` / ``agent_ref`` recorded on the invocation. That shared core
lives here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.protocol import MAX_RESULT_EXCERPT_CHARS, SubAgentInvocation, SubagentStatus
from helix_agent.runtime.cancellation import (
    CANCELLATION_TOKEN_KEY,
    CancellationToken,
    RunCancelledError,
)
from orchestrator.errors import MaxStepsExceededError
from orchestrator.tools.registry import ToolContext, ToolResult
from orchestrator.trajectory import (
    TrajectoryOutcome,
    TrajectoryRecord,
    TrajectoryRecorder,
)

if TYPE_CHECKING:
    from orchestrator.agent_factory import BuiltAgent

logger = logging.getLogger(__name__)

#: Strong refs to in-flight child trajectory dispatch tasks (Mini-ADR J-21):
#: ``asyncio.create_task`` drops its return value, so we keep the task in a
#: module set until it completes — otherwise GC may finalize it before the
#: ObjectStore put returns.
_BACKGROUND_TRAJECTORY_TASKS: set[asyncio.Task[None]] = set()

#: Wall-clock cap on one child trajectory dispatch.
_TRAJECTORY_DISPATCH_TIMEOUT_S: float = 5.0


async def run_child_to_result(
    *,
    child: BuiltAgent,
    task: str,
    ctx: ToolContext,
    child_depth: int,
    label: str,
    agent_ref: str,
    trajectory_recorder: TrajectoryRecorder | None,
    trajectory_metadata: Mapping[str, Any],
    extra_meta: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Run ``child`` to completion on a fresh thread seeded with ``task``.

    ``label`` / ``agent_ref`` are recorded on the :class:`SubAgentInvocation`
    (a static sub-agent passes its tool name + ``name@version``; a dynamic
    worker passes its worker label + a ``dynamic:<role>`` marker).
    ``extra_meta`` is merged into the result ``meta`` (e.g. ``{"dynamic":
    True, "role": ...}``).

    A child that exhausts its ``max_steps`` is a *partial result*, not a
    tool failure — its partial-progress note returns as a normal
    ``ToolResult`` so the parent can reason about it. A cancellation
    re-raises (the parent run tears down anyway).
    """
    sub_thread_id = uuid4()
    sub_run_id = uuid4()
    child_config = _child_config(ctx, sub_thread_id=sub_thread_id, sub_run_id=sub_run_id)
    child_input: dict[str, Any] = {
        "messages": [
            SystemMessage(content=child.system_prompt),
            HumanMessage(content=task),
        ],
        "step_count": 0,
        "max_steps": child.max_steps,
    }

    started_at = datetime.now(UTC)
    start_monotonic = time.monotonic()
    result: Any = None
    raised_max_steps = False

    try:
        result = await child.graph.ainvoke(child_input, child_config)
        outcome: TrajectoryOutcome = "success"
    except MaxStepsExceededError:
        outcome = "max_steps"
        raised_max_steps = True
        logger.info("child_run.max_steps label=%s agent_ref=%s", label, agent_ref)
    except RunCancelledError:
        partial_msgs, partial_steps = await _fetch_partial(child.graph, child_config, label=label)
        _dispatch_trajectory(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            sub_thread_id=sub_thread_id,
            sub_run_id=sub_run_id,
            outcome="cancelled",
            messages=partial_msgs,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            step_count=partial_steps,
            recorder=trajectory_recorder,
            metadata=trajectory_metadata,
        )
        raise

    wall_clock_ms = int((time.monotonic() - start_monotonic) * 1000)
    finished_at = datetime.now(UTC)
    if result is not None and isinstance(result, Mapping):
        messages: Sequence[BaseMessage] = list(result.get("messages", []))
        step_count = int(result.get("step_count", 0) or 0)
    else:
        messages, step_count = await _fetch_partial(child.graph, child_config, label=label)

    llm_call_count = sum(1 for msg in messages if isinstance(msg, AIMessage))

    _dispatch_trajectory(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        sub_thread_id=sub_thread_id,
        sub_run_id=sub_run_id,
        outcome=outcome,
        messages=messages,
        started_at=started_at,
        finished_at=finished_at,
        step_count=step_count,
        recorder=trajectory_recorder,
        metadata=trajectory_metadata,
    )

    meta: dict[str, Any] = {
        "subagent": label,
        "iteration_used": step_count,
        "llm_call_count": llm_call_count,
        "wall_clock_ms": wall_clock_ms,
    }
    if extra_meta:
        meta.update(extra_meta)

    answer = _final_answer(messages)
    if raised_max_steps:
        meta["subagent_max_steps"] = True
        return _build_tool_result(
            content=(
                f"[sub-agent {label!r} reached its step limit before producing a final answer]"
            ),
            meta=meta,
            status=SubagentStatus.FAILED,
            label=label,
            agent_ref=agent_ref,
            child_depth=child_depth,
            sub_thread_id=sub_thread_id,
            sub_run_id=sub_run_id,
            result_excerpt="",
            error=f"reached step limit before producing a final answer ({step_count} steps)",
            started_at=started_at,
            finished_at=finished_at,
            iteration_used=step_count,
            llm_call_count=llm_call_count,
            wall_clock_ms=wall_clock_ms,
        )

    if answer is None:
        meta["subagent_empty"] = True
        return _build_tool_result(
            content=f"[sub-agent {label!r} produced no answer]",
            meta=meta,
            status=SubagentStatus.COMPLETED,
            label=label,
            agent_ref=agent_ref,
            child_depth=child_depth,
            sub_thread_id=sub_thread_id,
            sub_run_id=sub_run_id,
            result_excerpt="",
            error=None,
            started_at=started_at,
            finished_at=finished_at,
            iteration_used=step_count,
            llm_call_count=llm_call_count,
            wall_clock_ms=wall_clock_ms,
        )

    return _build_tool_result(
        content=answer,
        meta=meta,
        status=SubagentStatus.COMPLETED,
        label=label,
        agent_ref=agent_ref,
        child_depth=child_depth,
        sub_thread_id=sub_thread_id,
        sub_run_id=sub_run_id,
        result_excerpt=answer[:MAX_RESULT_EXCERPT_CHARS],
        error=None,
        started_at=started_at,
        finished_at=finished_at,
        iteration_used=step_count,
        llm_call_count=llm_call_count,
        wall_clock_ms=wall_clock_ms,
    )


def _build_tool_result(
    *,
    content: str,
    meta: dict[str, Any],
    status: SubagentStatus,
    label: str,
    agent_ref: str,
    child_depth: int,
    sub_thread_id: UUID,
    sub_run_id: UUID,
    result_excerpt: str,
    error: str | None,
    started_at: datetime,
    finished_at: datetime,
    iteration_used: int,
    llm_call_count: int,
    wall_clock_ms: int,
) -> ToolResult:
    invocation = SubAgentInvocation(
        task_id=sub_run_id,
        sub_thread_id=sub_thread_id,
        name=label,
        agent_ref=agent_ref,
        child_depth=child_depth,
        status=status,
        result_excerpt=result_excerpt,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
        iteration_used=iteration_used,
        llm_call_count=llm_call_count,
        wall_clock_ms=wall_clock_ms,
    )
    return ToolResult(
        content=content,
        meta=meta,
        state_updates={"subagent_invocations": [invocation]},
    )


async def _fetch_partial(
    graph: Any, config: RunnableConfig, *, label: str
) -> tuple[list[BaseMessage], int]:
    """Best-effort read of a partial child state — Mini-ADR J-21."""
    aget_state = getattr(graph, "aget_state", None)
    if aget_state is None:
        return [], 0
    try:
        snapshot = await aget_state(config)
    except Exception as exc:
        logger.warning("child_run.fetch_partial_failed label=%s err=%s", label, type(exc).__name__)
        return [], 0
    values = getattr(snapshot, "values", None)
    if not isinstance(values, Mapping):
        return [], 0
    msgs = list(values.get("messages", []))
    step_count = int(values.get("step_count", 0) or 0)
    return msgs, step_count


def _dispatch_trajectory(
    *,
    tenant_id: UUID | None,
    user_id: UUID | None,
    sub_thread_id: UUID,
    sub_run_id: UUID,
    outcome: TrajectoryOutcome,
    messages: Sequence[BaseMessage],
    started_at: datetime,
    finished_at: datetime,
    step_count: int,
    recorder: TrajectoryRecorder | None,
    metadata: Mapping[str, Any],
) -> None:
    """Schedule a fire-and-forget L7 trajectory write for the child run."""
    if recorder is None or tenant_id is None:
        return
    record = TrajectoryRecord(
        thread_id=sub_thread_id,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=sub_run_id,
        outcome=outcome,
        messages=list(messages),
        started_at=started_at,
        finished_at=finished_at,
        step_count=step_count,
        metadata=dict(metadata),
    )
    task = asyncio.create_task(_record_safe(recorder, record))
    _BACKGROUND_TRAJECTORY_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TRAJECTORY_TASKS.discard)


async def _record_safe(recorder: TrajectoryRecorder, record: TrajectoryRecord) -> None:
    try:
        async with asyncio.timeout(_TRAJECTORY_DISPATCH_TIMEOUT_S):
            await recorder.record(record)
    except (TimeoutError, asyncio.CancelledError):
        logger.warning(
            "child_run.trajectory_dispatch_timeout label=%s",
            record.metadata.get("subagent_name", "?"),
        )


def _final_answer(messages: Sequence[BaseMessage]) -> str | None:
    """Return the last ``AIMessage``'s content as text, or ``None``."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return None


def _child_config(ctx: ToolContext, *, sub_thread_id: UUID, sub_run_id: UUID) -> RunnableConfig:
    """Build the child run's ``RunnableConfig`` — shares the parent's
    cancellation token + deadline so a parent cancel reaches every child
    node and the whole delegation tree honours one wall-clock cap."""
    token = ctx.cancellation_token or CancellationToken()
    configurable: dict[str, Any] = {
        CANCELLATION_TOKEN_KEY: token,
        "thread_id": str(sub_thread_id),
        "run_id": str(sub_run_id),
        "tenant_id": str(ctx.tenant_id),
    }
    if ctx.user_id is not None:
        configurable["user_id"] = str(ctx.user_id)
    # MCP-OAUTH (OA-3b-后续): carry the caller's OAuth subject so the child's
    # tool context resolves the same per-user OAuth pool as the parent.
    if ctx.oauth_user_id is not None:
        configurable["oauth_user_id"] = ctx.oauth_user_id
    if ctx.deadline_at is not None:
        configurable["deadline_at"] = ctx.deadline_at
    return {"configurable": configurable}
