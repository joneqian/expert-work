"""Canonical LangGraph state shape for orchestrator graphs.

Per [STREAM-E-DESIGN § 2.3](../../../../docs/streams/STREAM-E-DESIGN.md),
fields are added incrementally across the Stream E sub-PRs:

- **E.1**: ``messages`` (LangGraph reducer-style append)
- **E.6**: ``step_count`` + ``max_steps`` for the ReAct loop guard

Every ``AgentState`` channel is checkpointed (dill), so **non-serialisable
runtime objects do not live here**. They travel via the
``config["configurable"]`` channel instead — it is per-invocation and not
checkpointed:

- Tenant binding (``tenant_id`` / ``session_id`` / ``run_id``) — LangGraph idiom.
- ``cancellation_token`` (E.15) — backed by a live ``asyncio.Event``.
- The ``LLMRouter`` holds its own provider chain + fallback state (E.11).
"""

from __future__ import annotations

from operator import add
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from helix_agent.protocol import MemoryItem, Plan, Reflection, SubAgentInvocation
from orchestrator.tools.mutation_classifier import MutationOutcome

#: Default ReAct hard limit — see Mini-ADR E-6 in the design doc + the
#: "ReAct 无限循环" risk row. Manifest may override per-agent.
DEFAULT_MAX_STEPS = 20


class AgentState(TypedDict):
    """State threaded through every orchestrator LangGraph node.

    ``messages`` uses LangGraph's ``add_messages`` reducer so nodes
    returning ``{"messages": [...]}`` append to (rather than overwrite)
    the conversation history. ``step_count`` and ``max_steps`` use the
    default overwrite reducer — the agent node sets the new count each
    turn, and ``max_steps`` is configured once at graph construction.

    ``plan`` (Stream J.1) is set once by the ``planner`` node when the
    manifest's ``workflow.type`` is ``plan_execute``; it is absent for
    plain ``react`` graphs. ``NotRequired`` so the ReAct input shape is
    unchanged — readers use ``state.get("plan")``.

    ``reflections`` (Stream J.2) accumulates one :class:`Reflection` per
    ``reflect`` node entry — an ``operator.add`` reducer appends. Absent
    unless the manifest carries a ``reflection:`` block.

    ``recalled_memories`` (Stream J.3) is set once by the ``memory_recall``
    node — the long-term memories ``agent_node`` renders into its system
    context. Absent unless the manifest enables long-term memory.

    ``step_count_refund_pending`` (Stream L.L5 / Mini-ADR L-5) is the
    narrow channel a ``tools_node`` writes when one or more tools
    returned :attr:`~orchestrator.tools.registry.ToolResult.refund_iterations`
    greater than zero. The next ``agent_node`` subtracts it from
    ``step_count`` (clamped at 0) before computing the post-turn count,
    then resets the channel to ``0``. Keeps refund accounting
    observable and auditable instead of letting tools rewrite
    ``step_count`` directly.

    ``failed_mutations`` (Stream L.L4 / Mini-ADR L-4) accumulates
    :class:`~orchestrator.tools.mutation_classifier.MutationOutcome`
    rows for file-mutation tool calls that did NOT land in the most
    recent ``tools`` batch. The next ``agent_node`` reads the list,
    emits an ``<mutation-advisory>`` ``HumanMessage`` so the model
    cannot claim success on those paths, and resets the channel to
    ``[]``. Defaults to empty; tools_node only writes when at least
    one mutation failed.

    ``subagent_invocations`` (Stream J.4-补强-2 / Mini-ADR J-40)
    accumulates one
    :class:`~helix_agent.protocol.subagent.SubAgentInvocation` per
    SubAgentTool delegation — every outcome path (success / max_steps /
    cancelled / future timed_out) appends a terminal-state row via the
    ``operator.add`` reducer. Lets the parent's LangGraph checkpoint
    carry the full delegation history (audit + J.13 eval replay), and
    feeds future M2-B fan-in aggregation (iteration_used sum /
    llm_call_count sum / wall_clock_ms max). Absent unless the manifest
    declares ``subagents``.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    max_steps: int
    plan: NotRequired[Plan | None]
    reflections: NotRequired[Annotated[list[Reflection], add]]
    recalled_memories: NotRequired[list[MemoryItem]]
    step_count_refund_pending: NotRequired[int]
    failed_mutations: NotRequired[list[MutationOutcome]]
    subagent_invocations: NotRequired[Annotated[list[SubAgentInvocation], add]]
