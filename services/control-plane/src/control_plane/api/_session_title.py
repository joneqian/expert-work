"""Shared helpers: derive a human session title from message text.

Used by the run trigger (auto-title the thread from its first user message)
and the session-history list (lazy backfill of pre-existing threads that were
created before auto-titling existed — read their durable checkpoint's first
user message and persist it).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger("helix.control_plane.session_title")


def title_from_text(text: str, *, limit: int = 80) -> str:
    """A compact single-line title from message text.

    Collapses all runs of whitespace (incl. newlines) to single spaces, strips,
    truncates to ``limit`` chars.
    """
    single = " ".join(text.split())
    return single[:limit]


def message_text(content: Any) -> str:
    """Flatten a LangChain message ``content`` (str or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


async def first_message_title(
    checkpointer: BaseCheckpointSaver[Any],
    thread_id: UUID,
    *,
    limit: int = 80,
) -> str | None:
    """The thread's first user message as a title, or ``None``.

    Reads the durable checkpoint directly (keyed by ``thread_id``) and returns
    the first ``human`` message's text, truncated. Best-effort: any failure
    (no checkpoint, read error, no human turn) degrades to ``None``.
    """
    config: RunnableConfig = {"configurable": {"thread_id": str(thread_id), "checkpoint_ns": ""}}
    try:
        tup = await checkpointer.aget_tuple(config)
    except Exception:
        logger.warning("session_title.read_failed", exc_info=True)
        return None
    if tup is None:
        return None
    raw = (tup.checkpoint.get("channel_values") or {}).get("messages", [])
    for m in raw:
        if getattr(m, "type", None) != "human":
            continue
        title = title_from_text(message_text(getattr(m, "content", "")), limit=limit)
        if title:
            return title
    return None
