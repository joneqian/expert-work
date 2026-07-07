"""Sub-agent invocation models — Stream J.4-补强-2 (Mini-ADR J-40).

When a parent agent delegates to one or more :class:`SubAgentTool` in a
single LLM turn, each delegation produces one :class:`SubAgentInvocation`
that lands in the parent's ``AgentState.subagent_invocations`` channel.
The channel is an ``Annotated[list, operator.add]`` reducer (same shape
as the J.2 ``reflections`` channel), so the parent's LangGraph checkpoint
carries the full delegation history — solving the audit / replay gap
identified vs DeerFlow 2.0's ThreadState model.

:class:`SubagentStatus` mirrors the DeerFlow 2.0 6-state enum
(``PENDING / RUNNING / COMPLETED / FAILED / CANCELLED / TIMED_OUT``).
expert_work's 3-state outcome label (``success / max_steps / cancelled``,
introduced in Mini-ADR J-21) maps onto this enum at the SubAgentTool
emit site:

* ``success`` → ``COMPLETED``
* ``max_steps`` → ``FAILED`` (with ``error`` describing the step-limit hit)
* ``cancelled`` → ``CANCELLED`` (parent token / abort)
* (future) deadline expiry → ``TIMED_OUT`` (Mini-ADR J-40 deadline path)

``PENDING`` and ``RUNNING`` are reserved for fire-and-forget paths that
emit progress before completion; the current J.4 SubAgentTool path emits
a single terminal-state row per invocation, so those values are
present-but-unused in M0. Both will populate when streaming sub-agent
progress lands (M2-B follow-up; design § 11.2 Out-of-scope).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SubagentStatus(StrEnum):
    """Terminal (or in-flight) status of one sub-agent delegation."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class SubAgentInvocation(BaseModel):
    """One delegation lifecycle record — Mini-ADR J-40 fan-in payload.

    Emitted by :class:`SubAgentTool.call` via ``ToolResult.state_updates``
    on every outcome path (success / max_steps / cancelled / future
    timed_out). The parent's tools node promotes the entry into
    ``AgentState.subagent_invocations`` via the reducer.

    ``result_excerpt`` is the child's final answer truncated to
    :data:`MAX_RESULT_EXCERPT_CHARS` so a long sub-agent response doesn't
    bloat every parent checkpoint snapshot — the full answer remains in
    the parent's ``messages`` list as a ``ToolMessage`` and in L7
    trajectory ObjectStore.
    """

    model_config = ConfigDict(frozen=True)

    task_id: UUID = Field(description="sub-run's run_id, generated per delegation (PR #220)")
    sub_thread_id: UUID = Field(description="sub-run's thread_id, generated per delegation")
    name: str = Field(description="SubAgentSpec.name — the tool name the parent LLM saw")
    agent_ref: str = Field(
        description='"name@version" of the deployed AgentSpec the child resolved to',
    )
    child_depth: int = Field(ge=0, description="parent_depth + 1; bounded by MAX_SUBAGENT_DEPTH")
    status: SubagentStatus
    result_excerpt: str = Field(
        default="",
        description="truncated child answer; full text in parent messages + L7 trajectory",
    )
    error: str | None = Field(
        default=None,
        description="filled on FAILED / TIMED_OUT; None on COMPLETED / CANCELLED",
    )
    started_at: datetime
    finished_at: datetime | None = Field(
        default=None,
        description="None only when status in (PENDING, RUNNING)",
    )
    iteration_used: int = Field(ge=0, default=0, description="child state['step_count']")
    llm_call_count: int = Field(ge=0, default=0, description="AIMessage count in child messages")
    wall_clock_ms: int = Field(
        ge=0,
        default=0,
        description="time.monotonic delta around child ainvoke",
    )


#: Cap on :attr:`SubAgentInvocation.result_excerpt` length. 2 KiB keeps a
#: full delegation tree of (3 ^ 3 = 27 invocations x 2 KiB) ~ 54 KiB under
#: a 64 KiB Postgres row budget -- safe for the checkpoint serializer.
MAX_RESULT_EXCERPT_CHARS: int = 2048
