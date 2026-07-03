"""Shared transcript extraction — checkpoint blob → user/assistant turns.

One extraction path for the two consumers of a thread's conversation
history, so their notion of "a transcript turn" can't drift:

- ``GET /v1/sessions/{thread_id}/messages`` (Playground history + the
  conversation-detail transcript panel);
- :class:`control_plane.transcript_mirror_sweep.TranscriptMirrorSweep`
  (the ``thread_message`` mirror feeding content search — IA M4).

The ``messages`` channel uses the ``add_messages`` append reducer, so the
latest checkpoint carries the full history in one ``aget_tuple`` and a
message's index (``seq``) is stable across reads — the mirror's idempotency
key. Only human/ai turns with non-empty text survive; tool/system messages
stay in the per-run event stream by design.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

from control_plane.api._session_title import message_text
from helix_agent.persistence import MessageTurn


async def read_turns(
    checkpointer: BaseCheckpointSaver[Any],
    thread_id: UUID,
    *,
    include_hidden: bool = True,
) -> list[MessageTurn]:
    """Read a thread's user/assistant text turns off its durable checkpoint.

    Raises on checkpointer failure — callers pick their own degradation
    (the endpoint returns an empty transcript; the sweep skips the thread
    and retries next cycle).

    ``include_hidden`` (default ``True``) keeps the extraction *faithful*.
    RT-2 PR-4 (RT-ADR-9) marks orchestrator-authored scaffolding persisted
    into the checkpoint — e.g. the CM-1 ``<recovery-advisory>`` ``HumanMessage``
    — with ``helix_hide_from_ui``. That scaffolding must stay in the durable
    record, the search/audit mirror (``TranscriptMirrorSweep``) and the
    cross-tenant audit drill-in, so faithful is the *safe default*: a new
    persistence/audit caller that forgets the flag can never silently drop
    content from the audited record. Only the UI bubble view opts out
    (``include_hidden=False``) so scaffolding doesn't render as a turn — the
    raw record still carries it and the model always sees it in-prompt. This
    mirrors deer-flow, which reads the checkpoint faithfully and applies the
    ``hide_from_ui`` visibility filter only at its UI-serving router.
    """
    config: RunnableConfig = {"configurable": {"thread_id": str(thread_id), "checkpoint_ns": ""}}
    tup = await checkpointer.aget_tuple(config)
    if tup is None:
        return []
    raw = (tup.checkpoint.get("channel_values") or {}).get("messages", [])
    out: list[MessageTurn] = []
    for seq, m in enumerate(raw):
        mtype = getattr(m, "type", None)
        if mtype not in ("human", "ai"):
            continue
        if not include_hidden:
            kwargs = getattr(m, "additional_kwargs", None) or {}
            if kwargs.get("helix_hide_from_ui"):
                continue
        text = message_text(getattr(m, "content", ""))
        if text.strip():
            out.append(
                MessageTurn(seq=seq, role="user" if mtype == "human" else "assistant", content=text)
            )
    return out


__all__ = ["read_turns"]
