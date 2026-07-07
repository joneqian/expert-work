"""LangGraph execution surface — the orchestrator's only graph entrypoint.

Per [STREAM-E-DESIGN § 4.1](../../../../docs/streams/STREAM-E-DESIGN.md),
``GraphRunner`` centralises the wiring between a user-supplied
:class:`langgraph.graph.StateGraph` and the shared checkpointer so that
every compiled graph in the service writes to the same durable store.

The current sub-PR (E.1) exposes only ``checkpointer`` — later sub-PRs
extend ``__init__`` with the middleware chain (E.2), audit logger (E.5),
LLM router (E.11) and tool registry (E.7+). Callers must pass keyword
arguments so future widening is non-breaking.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrator.resume import sanitize_dangling_tool_calls

logger = logging.getLogger(__name__)


class GraphRunner:
    """Compile orchestrator state graphs against a shared checkpointer.

    Constructed once per service process (or per FastAPI lifespan) with
    a saver produced by
    :func:`expert_work.runtime.checkpointer.make_checkpointer`. Every
    ``compile`` call attaches the same saver so all graph runs in the
    service share one durable checkpoint store.
    """

    def __init__(self, *, checkpointer: BaseCheckpointSaver[Any]) -> None:
        self._checkpointer = checkpointer

    @property
    def checkpointer(self) -> BaseCheckpointSaver[Any]:
        return self._checkpointer

    def compile(
        self,
        graph: StateGraph[Any, Any, Any, Any],
    ) -> CompiledStateGraph[Any, Any, Any, Any]:
        """Compile ``graph`` with the configured checkpointer.

        Equivalent to ``graph.compile(checkpointer=self.checkpointer)``,
        but routing through ``GraphRunner`` keeps the checkpointer wiring
        in a single place — later sub-PRs (E.2+) inject middleware and
        cancellation plumbing here too, so callers don't need to know
        the evolving set of compile-time hooks.
        """
        compiled: CompiledStateGraph[Any, Any, Any, Any] = graph.compile(
            checkpointer=self._checkpointer,
        )
        logger.debug(
            "orchestrator.graph.compiled checkpointer=%s",
            type(self._checkpointer).__name__,
        )
        return compiled

    async def sanitize_thread(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        config: RunnableConfig,
        *,
        as_node: str = "tools",
    ) -> int:
        """Repair orphan ``tool_calls`` in a thread's checkpoint (E.15).

        Call before resuming a thread that may have been cancelled
        mid-tool-dispatch. Reads the thread's current state, computes
        placeholder ``ToolMessage``s for any unanswered ``tool_calls``
        (see :func:`orchestrator.resume.sanitize_dangling_tool_calls`),
        and — if any — appends them via ``aupdate_state``.

        The placeholders are written ``as_node="tools"`` (the ReAct
        graph's tool node): LangGraph then positions the thread as
        "tools just finished", so the resumed run flows ``tools →
        agent`` and the agent re-reasons over the now-valid history.
        Writing them as the agent node instead would route the
        conditional edge to ``END`` (the last message is no longer an
        AIMessage with tool_calls) and the run would not resume.

        Returns the number of placeholders injected (``0`` when the
        thread's history is already valid, including a fresh thread
        with no checkpoint).
        """
        snapshot = await graph.aget_state(config)
        values = snapshot.values if isinstance(snapshot.values, dict) else {}
        messages = values.get("messages") or []
        placeholders = sanitize_dangling_tool_calls(messages)
        if placeholders:
            await graph.aupdate_state(config, {"messages": placeholders}, as_node=as_node)
            logger.info(
                "orchestrator.resume.sanitized count=%d thread=%s",
                len(placeholders),
                (config.get("configurable") or {}).get("thread_id"),
            )
        return len(placeholders)
