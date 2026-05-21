"""Sub-agent delegation — Stream J.4 (agent-as-tool).

A manifest's ``spec.subagents`` block declares deployed agents the parent
may delegate to. The assembler (Stream J.4 PR4) wraps each entry into a
named :class:`SubAgentTool` so the parent's LLM sees delegation as an
ordinary tool call.

This module holds:

* :data:`MAX_SUBAGENT_DEPTH` — the hard recursion cap.
* :class:`ChildAgentBuilder` — the callback the control-plane injects so
  the orchestrator can resolve an ``agent_ref`` and build the referenced
  sub-agent (``AgentSpecStore`` lives in the control-plane; the
  orchestrator only ever holds pre-built ``BuiltAgent``\\s).
* :class:`SubAgentTool` — the :class:`~orchestrator.tools.registry.Tool`
  adapter: ``call()`` builds the child agent, runs it to completion on
  its own thread, and returns the child's final answer to the parent.

See [STREAM-J-DESIGN §11 / Mini-ADR J-12](../../../../../docs/streams/STREAM-J-DESIGN.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.protocol import (
    MAX_RESULT_EXCERPT_CHARS,
    SubAgentInvocation,
    SubAgentSpec,
    SubagentStatus,
    parse_agent_ref,
)
from helix_agent.runtime.cancellation import (
    CANCELLATION_TOKEN_KEY,
    CancellationToken,
    RunCancelledError,
)
from orchestrator.errors import MaxStepsExceededError
from orchestrator.tools.registry import ToolBlockedError, ToolContext, ToolResult, ToolSpec
from orchestrator.trajectory import (
    TrajectoryOutcome,
    TrajectoryRecord,
    TrajectoryRecorder,
)

if TYPE_CHECKING:
    from orchestrator.agent_factory import BuiltAgent

logger = logging.getLogger(__name__)

#: Strong refs to in-flight sub-agent trajectory dispatch tasks (Mini-ADR
#: J-21). Mirrors the orchestrator.sse pattern: ``asyncio.create_task``
#: drops its return value, so we keep the task in a module set until it
#: completes — otherwise GC may finalize the task before the ObjectStore
#: put returns.
_BACKGROUND_TRAJECTORY_TASKS: set[asyncio.Task[None]] = set()

#: Wall-clock cap on one sub-agent trajectory dispatch. Same rationale as
#: ``orchestrator.sse._TRAJECTORY_DISPATCH_TIMEOUT_S`` — a hung ObjectStore
#: must not pin a background task forever; the recorder swallows its own
#: errors so the deadline is the outer guard.
_TRAJECTORY_DISPATCH_TIMEOUT_S: float = 5.0

#: Hard cap on recursive sub-agent delegation depth. The top-level agent
#: builds at depth 0; each delegation step builds the child at parent
#: depth + 1. An agent built at this depth gets **no** ``SubAgentTool``
#: registered — structural recursion termination, so a cross-manifest
#: cycle (A->B->A) can never run away (Mini-ADR J-12). This replaces a
#: token-budget guard: helix has no runtime token budget, so cost is
#: bounded structurally by depth times each agent's ``max_iterations``.
MAX_SUBAGENT_DEPTH: Final = 3


@runtime_checkable
class ChildAgentBuilder(Protocol):
    """Resolves an ``agent_ref`` and builds the referenced sub-agent.

    Injected into :class:`~orchestrator.tools.ToolEnv` by the
    control-plane — the orchestrator cannot resolve an ``agent_ref``
    itself (the ``AgentSpecStore`` is control-plane-only). :class:`SubAgentTool`
    calls this inside ``call()`` to obtain the child ``BuiltAgent`` it
    delegates to.

    ``depth`` is the child's build-time recursion depth (parent depth
    + 1). The builder keys its agent cache on ``depth`` because the same
    manifest builds a *different* graph at different depths — an agent
    built at :data:`MAX_SUBAGENT_DEPTH` carries no further
    ``SubAgentTool``\\s.
    """

    async def __call__(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        depth: int,
    ) -> BuiltAgent:
        """Build the sub-agent ``name@version`` for ``tenant_id`` at ``depth``.

        Raises if the ``agent_ref`` does not resolve to a deployed,
        non-deleted AgentSpec — :class:`SubAgentTool` lets that propagate
        so the parent's tools node turns it into a tool error rather than
        crashing the parent run.
        """


@dataclass(frozen=True)
class SubAgentTool:
    """A :class:`~orchestrator.tools.registry.Tool` that delegates to a
    deployed sub-agent — Stream J.4.

    One instance per ``SubAgentSpec`` declared in the parent manifest's
    ``spec.subagents`` block. ``call()`` builds the referenced child
    agent via :attr:`builder`, runs it to completion on a fresh thread
    seeded with the delegated ``task``, and returns the child's final
    answer as the tool result.

    The child run reuses the parent's :class:`CancellationToken` so a
    parent cancel propagates into every child node. A child that hits
    its own ``max_steps`` is **not** a tool error — its partial-progress
    note is returned as a normal :class:`ToolResult` so the parent can
    reason about it; a cancellation is left to propagate.
    """

    subagent: SubAgentSpec
    builder: ChildAgentBuilder
    #: The child's build-time recursion depth (parent depth + 1). Passed
    #: straight to :attr:`builder` so the depth cap is enforced there.
    child_depth: int
    #: Mini-ADR J-21 — when set, the child run's trajectory writes to its
    #: own L7 ObjectStore key (``{prefix}/{tenant}/{outcome}/{date}/
    #: {sub_thread_id}.jsonl``). Fire-and-forget dispatch; ``None`` is a
    #: valid deployment (the recorder is not configured) — no trajectory
    #: rows for sub-agent runs, parent run trajectory still records via
    #: the orchestrator's SSE worker.
    trajectory_recorder: TrajectoryRecorder | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.subagent.name,
            description=self.subagent.description,
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "The subtask to delegate, described in full. The "
                            "sub-agent runs fresh with only this text as its "
                            "instruction — it sees none of this conversation."
                        ),
                    },
                },
                "required": ["task"],
            },
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        if ctx.tenant_id is None:
            msg = (
                f"sub-agent {self.subagent.name!r} cannot be delegated to without a tenant binding"
            )
            raise ToolBlockedError(msg)
        task = self._require_task(args)
        name, version = parse_agent_ref(self.subagent.agent_ref)

        child = await self.builder(
            tenant_id=ctx.tenant_id,
            name=name,
            version=version,
            depth=self.child_depth,
        )
        # Generate the sub-run's thread_id / run_id up front so the L7
        # trajectory dispatch (Mini-ADR J-21) keys on the same UUIDs as
        # the child's RunnableConfig.
        sub_thread_id = uuid4()
        sub_run_id = uuid4()
        child_config = self._child_config(ctx, sub_thread_id=sub_thread_id, sub_run_id=sub_run_id)
        child_input: dict[str, Any] = {
            "messages": [
                SystemMessage(content=child.system_prompt),
                HumanMessage(content=task),
            ],
            "step_count": 0,
            "max_steps": child.max_steps,
        }

        # Mini-ADR J-21 — budget telemetry: wall-clock around the whole
        # child run so even a cancelled / max_steps path still contributes
        # a real number.
        started_at = datetime.now(UTC)
        start_monotonic = time.monotonic()
        result: Any = None
        raised_max_steps = False

        try:
            result = await child.graph.ainvoke(child_input, child_config)
            outcome: TrajectoryOutcome = "success"
        except MaxStepsExceededError:
            # A child that runs out of steps is a *partial result*, not
            # a tool failure — emit a partial-progress trajectory + meta
            # so the parent can see how much budget was burned.
            outcome = "max_steps"
            raised_max_steps = True
            logger.info(
                "subagent.max_steps name=%s agent_ref=%s",
                self.subagent.name,
                self.subagent.agent_ref,
            )
        except RunCancelledError:
            # Cancellation tears the whole run down — must re-raise. But
            # Mini-ADR J-21 still wants the child's partial trajectory in
            # ObjectStore so J.13 eval can replay every sub-run, including
            # cancelled ones. Fire the dispatch (non-blocking) before
            # bubbling.
            partial_msgs, partial_steps = await self._fetch_partial(child.graph, child_config)
            self._dispatch_trajectory(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                sub_thread_id=sub_thread_id,
                sub_run_id=sub_run_id,
                outcome="cancelled",
                messages=partial_msgs,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                step_count=partial_steps,
            )
            raise

        wall_clock_ms = int((time.monotonic() - start_monotonic) * 1000)
        finished_at = datetime.now(UTC)
        if result is not None and isinstance(result, Mapping):
            messages: Sequence[BaseMessage] = list(result.get("messages", []))
            step_count = int(result.get("step_count", 0) or 0)
        else:
            messages, step_count = await self._fetch_partial(child.graph, child_config)

        llm_call_count = sum(1 for msg in messages if isinstance(msg, AIMessage))

        self._dispatch_trajectory(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            sub_thread_id=sub_thread_id,
            sub_run_id=sub_run_id,
            outcome=outcome,
            messages=messages,
            started_at=started_at,
            finished_at=finished_at,
            step_count=step_count,
        )

        meta: dict[str, Any] = {
            "subagent": self.subagent.name,
            "iteration_used": step_count,
            "llm_call_count": llm_call_count,
            "wall_clock_ms": wall_clock_ms,
        }

        answer = _final_answer(messages)
        if raised_max_steps:
            meta["subagent_max_steps"] = True
            return self._build_tool_result(
                content=(
                    f"[sub-agent {self.subagent.name!r} reached its step "
                    "limit before producing a final answer]"
                ),
                meta=meta,
                status=SubagentStatus.FAILED,
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
            return self._build_tool_result(
                content=f"[sub-agent {self.subagent.name!r} produced no answer]",
                meta=meta,
                status=SubagentStatus.COMPLETED,
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

        return self._build_tool_result(
            content=answer,
            meta=meta,
            status=SubagentStatus.COMPLETED,
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
        self,
        *,
        content: str,
        meta: dict[str, Any],
        status: SubagentStatus,
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
        """Build the terminal :class:`ToolResult` with the Mini-ADR J-40
        ``SubAgentInvocation`` appended to ``state_updates``.

        Cancellation does **not** go through here — it re-raises before a
        ToolResult is built (the parent run tears down anyway). The L7
        trajectory dispatch in :meth:`call` still records the cancelled
        sub-run for J.13 eval replay.
        """
        invocation = SubAgentInvocation(
            task_id=sub_run_id,
            sub_thread_id=sub_thread_id,
            name=self.subagent.name,
            agent_ref=self.subagent.agent_ref,
            child_depth=self.child_depth,
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
        self, graph: Any, config: RunnableConfig
    ) -> tuple[list[BaseMessage], int]:
        """Best-effort read of a partial child state — Mini-ADR J-21.

        Used by the ``RunCancelledError`` and ``max_steps`` paths where
        the child's ``ainvoke`` did not return a final state dict but the
        checkpointer still holds whatever the child managed to write.
        Mirrors the safe-fetch pattern in ``orchestrator.sse``.
        """
        aget_state = getattr(graph, "aget_state", None)
        if aget_state is None:
            return [], 0
        try:
            snapshot = await aget_state(config)
        except Exception as exc:
            logger.warning(
                "subagent.fetch_partial_failed name=%s err=%s",
                self.subagent.name,
                type(exc).__name__,
            )
            return [], 0
        values = getattr(snapshot, "values", None)
        if not isinstance(values, Mapping):
            return [], 0
        msgs = list(values.get("messages", []))
        step_count = int(values.get("step_count", 0) or 0)
        return msgs, step_count

    def _dispatch_trajectory(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID | None,
        sub_thread_id: UUID,
        sub_run_id: UUID,
        outcome: TrajectoryOutcome,
        messages: Sequence[BaseMessage],
        started_at: datetime,
        finished_at: datetime,
        step_count: int,
    ) -> None:
        """Schedule a fire-and-forget L7 trajectory write for the child run.

        Mini-ADR J-21: each sub-agent run lands its own ObjectStore object
        under ``{prefix}/{tenant_id}/{outcome}/{YYYY}/{MM}/{DD}/{sub_thread_id}.jsonl``
        so J.13 eval can replay every node in the delegation tree
        independently. ``None`` recorder makes the dispatch a no-op.
        """
        recorder = self.trajectory_recorder
        if recorder is None:
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
            metadata={
                "subagent_name": self.subagent.name,
                "subagent_ref": self.subagent.agent_ref,
                "child_depth": self.child_depth,
            },
        )
        task = asyncio.create_task(_record_subagent_safe(recorder, record))
        _BACKGROUND_TRAJECTORY_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TRAJECTORY_TASKS.discard)

    def _require_task(self, args: Mapping[str, Any]) -> str:
        raw = args.get("task")
        if not isinstance(raw, str) or not raw.strip():
            msg = f"sub-agent {self.subagent.name!r} requires a non-empty 'task' string"
            raise ValueError(msg)
        return raw.strip()

    def _child_config(
        self,
        ctx: ToolContext,
        *,
        sub_thread_id: UUID,
        sub_run_id: UUID,
    ) -> RunnableConfig:
        """Build the child run's ``RunnableConfig``.

        ``sub_thread_id`` / ``sub_run_id`` are generated by :meth:`call`
        so the L7 trajectory dispatch (Mini-ADR J-21) keys on the same
        UUIDs the child's checkpointer uses. The child **shares the
        parent's** ``CancellationToken``, so a parent cancel reaches
        every child node. ``tenant_id`` / ``user_id`` carry over so the
        child's own tools stay tenant-scoped. Linking the child run to
        the parent for audit is the control-plane's job (Stream J.4 PR5).
        """
        token = ctx.cancellation_token or CancellationToken()
        configurable: dict[str, Any] = {
            CANCELLATION_TOKEN_KEY: token,
            "thread_id": str(sub_thread_id),
            "run_id": str(sub_run_id),
            "tenant_id": str(ctx.tenant_id),
        }
        if ctx.user_id is not None:
            configurable["user_id"] = str(ctx.user_id)
        return {"configurable": configurable}


async def _record_subagent_safe(recorder: TrajectoryRecorder, record: TrajectoryRecord) -> None:
    """Background body for :meth:`SubAgentTool._dispatch_trajectory`.

    Wraps :meth:`TrajectoryRecorder.record` in a wall-clock cap so a slow
    ObjectStore put can't keep the task alive indefinitely; the recorder
    itself already swallows transport errors so this deadline is the
    outer guard.
    """
    try:
        async with asyncio.timeout(_TRAJECTORY_DISPATCH_TIMEOUT_S):
            await recorder.record(record)
    except (TimeoutError, asyncio.CancelledError):
        logger.warning(
            "subagent.trajectory_dispatch_timeout name=%s",
            record.metadata.get("subagent_name", "?"),
        )


def _final_answer(messages: Sequence[BaseMessage]) -> str | None:
    """Return the last ``AIMessage``'s content as text, or ``None``.

    The child graph ends on the agent's no-tool-calls ``AIMessage``;
    that message's content is the delegated answer.
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return None
