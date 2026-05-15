"""Canonical LangGraph state shape for orchestrator graphs.

Per [STREAM-E-DESIGN § 2.3](../../../../docs/streams/STREAM-E-DESIGN.md),
fields are added incrementally across the Stream E sub-PRs:

- **E.1**: ``messages`` (LangGraph reducer-style append)
- **E.6 (this PR)**: ``step_count`` + ``max_steps`` for the ReAct loop guard
- E.11: ``provider_chain`` / ``current_provider_idx`` for LLM fallback routing
- E.15: ``cancellation_token`` (runtime-only, ``__skip_checkpoint__``)

Tenant binding (``tenant_id`` / ``session_id`` / ``run_id``) is passed via
the ``config["configurable"]`` channel (LangGraph idiom) and does not live
on ``AgentState`` — graphs read it from ``RunnableConfig`` so the same
state shape works for any tenant.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

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
    """

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    max_steps: int
