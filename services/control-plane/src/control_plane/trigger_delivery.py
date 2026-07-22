"""Deliver a fired trigger's result back into the originating conversation.

Spec 1 PR3 (conversational scheduled tasks) — component D1. A scheduled task
runs in its own scratch thread; on success the scheduler's reconcile pass calls
:func:`inject_delivery` to append the run's result as an ``AIMessage`` into the
conversation the task was created from. Reuses LangGraph's ``aupdate_state``
(the same mechanism resume / plan-injection / sanitize use): it writes a new
checkpoint version with the message appended via the ``messages`` add-reducer —
no LLM turn, no history replay. The user sees it the next time they open the
conversation (the ``/messages`` endpoint reads the checkpoint directly).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph


async def inject_delivery(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    *,
    thread_id: UUID,
    tenant_id: UUID,
    result_text: str,
    source_run_id: UUID,
    trigger_id: UUID,
) -> None:
    """Append ``result_text`` as an ``AIMessage`` to ``thread_id``'s checkpoint.

    The message is tagged (but NOT hidden) so the UI shows it and callers can
    trace it back to the firing. ``as_node`` follows the codebase convention
    (``"agent"`` when the thread already has history, ``"__start__"`` otherwise).
    Real delivery targets are always existing conversations (a reuse_thread
    task's originating thread), so the has-history path — left in a clean
    turn-complete state after an assistant message with no tool calls — is the
    one that matters.
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": str(thread_id), "tenant_id": str(tenant_id)}
    }
    snapshot = await graph.aget_state(config)
    values = snapshot.values if isinstance(snapshot.values, dict) else {}
    has_history = bool(values.get("messages"))
    message = AIMessage(
        content=result_text,
        additional_kwargs={
            "expert_work_scheduled_delivery": True,
            "expert_work_source_run_id": str(source_run_id),
            "expert_work_trigger_id": str(trigger_id),
        },
    )
    await graph.aupdate_state(
        config,
        {"messages": [message]},
        as_node="agent" if has_history else "__start__",
    )


__all__ = ["inject_delivery"]
