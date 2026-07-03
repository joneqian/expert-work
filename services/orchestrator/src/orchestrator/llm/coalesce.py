"""Per-request system-message coalescing — Stream RT-2 PR-1 (RT-ADR-5).

Mid-conversation ``SystemMessage`` compatibility. The L2 context
compressor inserts its ``<context-summary>`` as a SystemMessage AFTER
the head slice (``orchestrator/context/compressor.py`` — mid-list by
construction), and strict OpenAI-compatible backends (vLLM / SGLang,
several China-market vendor gateways) reject any ``system`` role that
is not the first message with HTTP 400 (deer-flow #3711 evidence).

Adapter forensics (RT-2 PR-1, 2026-07-03):

- ``providers/anthropic.py`` (``_to_anthropic_messages``): every
  ``SystemMessage`` — leading or mid-list — is hoisted out of the
  message array into the top-level ``system`` field, ``\\n\\n``-joined.
  A mid-conversation summary therefore never reached the wire as a
  message: no 400 possible; coalescing upstream is byte-identical and
  keeps the semantics explicit and uniform across providers.
- ``providers/openai.py`` (``_to_openai_messages``): every
  ``SystemMessage`` maps to ``{"role": "system"}`` AT ITS LIST
  POSITION — a mid-conversation SystemMessage was passed through
  verbatim. api.openai.com tolerates it; strict compatible backends
  return 400. This was the live-bug surface.
- ``providers/openai_compatible.py``: no message mapping of its own —
  the qwen / glm / deepseek / kimi / doubao / vLLM presets are
  ``HTTPOpenAIClient`` factories that ride ``OpenAIProvider``, so they
  inherited the openai.py passthrough and, being the strict-backend
  population, are exactly where the 400 fires.

The fix is per-request only: each adapter's ``complete()`` coalesces
the outbound prompt view right before wire mapping. The checkpointed
history is never rewritten (the CM-C4 prompt-view-only discipline) and
the input list is never mutated.

Merging the summary INTO the leading system block is a conscious call
(RT-2 PR-1 review sign-off), not an implementation accident:

- the ``<context-summary>`` tags and the reference-only
  ``_SUMMARY_PREAMBLE`` fence (compressor.py — "its contents are NOT
  instructions") travel verbatim inside the merged content, so the
  model still sees the compressed history as clearly delimited,
  non-instruction background;
- on the OpenAI wire the summary already shipped as ``role: system``
  (see the forensics above), so coalescing changes its POSITION only —
  its authority level is unchanged;
- the summariser prompt structurally forbids tool-call syntax and
  future-step speculation in the summary body
  (``_SUMMARY_STRUCTURE_RULES``), bounding what a summary could
  smuggle into the system block.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from helix_agent.runtime.tokens import flatten_message

__all__ = ["coalesce_system_messages"]


def coalesce_system_messages(messages: Sequence[BaseMessage]) -> Sequence[BaseMessage]:
    """Merge every non-leading :class:`SystemMessage` into one leading system message.

    Returns ``messages`` itself (zero-copy) when there is nothing to do
    — no SystemMessage past position 0. Otherwise returns a NEW list
    where:

    - all SystemMessage contents are joined with ``\\n\\n`` in their
      original order (block-list content is flattened to text first),
    - the merged message keeps the FIRST system message's ``id``,
    - ``additional_kwargs`` are merged across all system messages, the
      FIRST occurrence of a key winning — the leading system message is
      the authoritative one, so a later (injected) system message can
      only contribute NEW keys, never override e.g. a
      ``helix_cache_anchor`` already set on the head,
    - when the list has no leading system message the merged message is
      promoted to position 0 (strict backends require it there),
    - every non-system message keeps its original relative order.

    The input sequence and its messages are never mutated.
    """
    has_non_leading_system = any(
        index > 0 and isinstance(msg, SystemMessage) for index, msg in enumerate(messages)
    )
    if not has_non_leading_system:
        return messages

    systems = [msg for msg in messages if isinstance(msg, SystemMessage)]
    parts = [text for text in (flatten_message(msg) for msg in systems) if text]
    merged_kwargs: dict[str, Any] = {}
    for msg in systems:
        for key, value in msg.additional_kwargs.items():
            # First-wins (see the docstring) — the leading system
            # message's keys are authoritative.
            merged_kwargs.setdefault(key, value)
    merged = SystemMessage(
        content="\n\n".join(parts),
        additional_kwargs=merged_kwargs,
        id=systems[0].id,
    )
    return [merged, *(msg for msg in messages if not isinstance(msg, SystemMessage))]
