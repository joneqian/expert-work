"""Memory nodes — Stream J.3 (long-term memory recall + write-back).

When a manifest enables long-term memory (``memory.long_term``) the
factory adds two graph nodes around the agent loop:

::

    START → [memory_recall] → ... agent ⇄ tools ... → [memory_writeback] → END

- ``memory_recall`` embeds the user's task, fetches the top-k nearest
  past memories, and parks them on ``AgentState.recalled_memories`` —
  ``agent_node`` renders them into its system context every step.
- ``memory_writeback`` makes one LLM call that extracts new durable
  memories from the run's trajectory, embeds them, and persists them.

Both no-op when the run carries no per-user scope (no ``user_id``) —
long-term memory is per-user. Recall / write-back are best-effort: any
failure is logged and swallowed so it never fails the run (cancellation
still propagates).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast
from uuid import uuid4

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.persistence import MemoryStore
from helix_agent.protocol import MemoryItem
from helix_agent.runtime.cancellation import RunCancelledError
from orchestrator.graph_builder._config import cancellation_token, configurable_uuid
from orchestrator.llm import Embedder, LLMCaller
from orchestrator.state import AgentState

logger = logging.getLogger(__name__)

#: A memory graph node: takes state + config, returns state updates.
MemoryNode = Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]

#: Per-message cap when rendering the trajectory for the extraction prompt.
_TRAJECTORY_CHAR_CAP = 1000

_EXTRACT_SYSTEM = (
    "You are a memory extraction module. From the conversation, extract "
    "durable, reusable memories worth recalling in future sessions — "
    'stable user facts or preferences ("fact"), and concise summaries of '
    'what was done or decided ("episodic"). Extract nothing trivial or '
    "ephemeral. Respond with ONLY a JSON object, no prose and no code "
    "fences:\n"
    '{"memories": [{"kind": "fact" | "episodic", "content": "<one concise '
    'sentence>"}]}\n'
    'If there is nothing worth remembering, return {"memories": []}.'
)


def _message_text(message: BaseMessage) -> str:
    content = message.content
    return content if isinstance(content, str) else str(content)


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


def _render_trajectory(messages: list[BaseMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        text = _message_text(message).strip()
        if len(text) > _TRAJECTORY_CHAR_CAP:
            text = text[:_TRAJECTORY_CHAR_CAP] + "...[truncated]"
        lines.append(f"[{message.type}] {text}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


def parse_extracted_memories(text: str) -> list[tuple[Literal["fact", "episodic"], str]]:
    """Parse the extraction LLM reply into ``(kind, content)`` pairs.

    Tolerant — any malformed reply yields ``[]`` (write-back is
    best-effort). Duplicate contents within the batch are dropped.
    """
    raw = _extract_json_object(text)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        rows = data["memories"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    out: list[tuple[Literal["fact", "episodic"], str]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind", "")).strip().lower()
        content = str(row.get("content", "")).strip()
        if kind in ("fact", "episodic") and content and content not in seen:
            seen.add(content)
            out.append((cast(Literal["fact", "episodic"], kind), content))
    return out


def make_memory_recall_node(
    *, memory_store: MemoryStore, embedder: Embedder, top_k: int
) -> MemoryNode:
    """Build the ``memory_recall`` node bound to the store + embedder."""

    async def memory_recall_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = cancellation_token(config)
        token.raise_if_cancelled()

        tenant_id = configurable_uuid(config, "tenant_id")
        user_id = configurable_uuid(config, "user_id")
        if tenant_id is None or user_id is None:
            return {}
        task = _last_human_text(list(state["messages"]))
        if not task:
            return {}
        try:
            vectors = await token.run_cancellable(embedder.embed([task]))
            memories = await memory_store.retrieve(
                tenant_id=tenant_id,
                user_id=user_id,
                query_embedding=vectors[0],
                limit=top_k,
            )
        except RunCancelledError:
            raise
        except Exception:
            logger.warning("memory.recall_failed — continuing without memories", exc_info=True)
            return {}
        logger.info("memory.recall count=%d", len(memories))
        return {"recalled_memories": memories}

    return memory_recall_node


def make_memory_writeback_node(
    *, memory_store: MemoryStore, embedder: Embedder, llm_caller: LLMCaller
) -> MemoryNode:
    """Build the ``memory_writeback`` node bound to the store + embedder."""

    async def memory_writeback_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = cancellation_token(config)
        token.raise_if_cancelled()

        tenant_id = configurable_uuid(config, "tenant_id")
        user_id = configurable_uuid(config, "user_id")
        if tenant_id is None or user_id is None:
            return {}
        thread_id = configurable_uuid(config, "thread_id")

        prompt = [
            SystemMessage(content=_EXTRACT_SYSTEM),
            HumanMessage(content=_render_trajectory(list(state["messages"]))),
        ]
        try:
            response = await token.run_cancellable(llm_caller(messages=prompt, tools=[]))
            extracted = parse_extracted_memories(_message_text(response))
            if not extracted:
                return {}
            vectors = await token.run_cancellable(
                embedder.embed([content for _, content in extracted])
            )
            items = [
                MemoryItem(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    kind=kind,
                    content=content,
                    embedding=vector,
                    source_thread_id=str(thread_id) if thread_id is not None else None,
                )
                for (kind, content), vector in zip(extracted, vectors, strict=True)
            ]
            await memory_store.write(items)
        except RunCancelledError:
            raise
        except Exception:
            logger.warning("memory.writeback_failed — run unaffected", exc_info=True)
            return {}
        logger.info("memory.writeback count=%d", len(items))
        return {}

    return memory_writeback_node
