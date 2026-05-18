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

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from helix_agent.protocol import Plan

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
    """

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    max_steps: int
    plan: NotRequired[Plan | None]
