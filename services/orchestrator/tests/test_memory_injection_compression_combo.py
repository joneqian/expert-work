"""Stream RT-2 PR-2 (RT-ADR-8) — memory injection x compression combo.

deer-flow #3746 showed injection and compression mechanisms blowing up
at their COMBINATION point, not in either unit. helix's per_session
memory block lands at ``messages[1]`` (the cache-anchor HumanMessage —
Mini-ADR U-8) and survives L2 compression only because it is the first
non-system message and ``head_keep >= 1`` keeps it in the head slice.
Nothing in the compressor knows about the anchor: these tests lock the
combined behavior — real ``_inject_memories`` output through a real
:class:`ContextCompressor` (scripted summariser) — so a head/tail
parameter change that would silently summarise the memory block away
fails loudly here.

The ``head_keep`` boundary cases (0 / 1):

* ``head_keep=1`` — the block survives, but the user's first task
  message falls into the summarised middle (pinned);
* ``head_keep=0`` (the protocol allows it: ``ge=0``) — RT-2 PR-4 (§8.4)
  now FLOORS ``head_keep`` to 1 in the factory when per_session recall is
  active (``floor_head_keep_for_injection``), so the effective compressor
  keeps the anchor. The direct-``head_keep=0`` compressor still loses the
  block (nothing in the compressor knows about the anchor — the fix lives
  one layer up, at construction); the test below drives the floor helper
  the factory applies and asserts the block survives the floored value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

import pytest
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

from helix_agent.protocol import MemoryItem
from orchestrator.context import ContextCompressor, floor_head_keep_for_injection
from orchestrator.graph_builder.builder import _inject_memories
from orchestrator.tools.registry import ToolSpec


@dataclass
class _ScriptedSummariser:
    """Deterministic summariser double — the compressor is real."""

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del messages, tools
        return AIMessage(content="- summarised middle")


def _memory(content: str) -> MemoryItem:
    return MemoryItem(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        kind="fact",
        content=content,
        embedding=(),
    )


def _injected_conversation() -> list[BaseMessage]:
    """A long conversation with the memory block injected per_session:
    ``[system, memory-block, task, filler x20]`` — over the compression
    threshold used below (chars//4 heuristic)."""
    history: list[BaseMessage] = [
        SystemMessage(content="sys"),
        HumanMessage(content="task"),
    ]
    for i in range(20):
        msg_type = HumanMessage if i % 2 else AIMessage
        history.append(msg_type(content=f"filler-{i} " + "x" * 490))
    return _inject_memories(history, [_memory("user prefers concise replies")])


def _compressor(*, head_keep: int) -> ContextCompressor:
    return ContextCompressor(
        llm_caller=_ScriptedSummariser(),
        context_window=3_000,  # threshold 2100 tokens; the fixture is ~2500
        threshold_pct=0.7,
        head_keep=head_keep,
        tail_keep=6,
    )


def _find_anchor_block(messages: Sequence[BaseMessage]) -> tuple[int, BaseMessage] | None:
    for idx, msg in enumerate(messages):
        if msg.additional_kwargs.get("helix_cache_anchor") is True:
            return idx, msg
    return None


def _has_summary(messages: Sequence[BaseMessage]) -> bool:
    return any(
        isinstance(m, SystemMessage) and "<context-summary>" in str(m.content) for m in messages
    )


@pytest.mark.asyncio
async def test_injected_block_survives_compression_in_head() -> None:
    """Default-shaped ``head_keep`` (>= 1): compression fires on the
    injected conversation and the ``messages[1]`` memory block stays in
    the head — same position, same content, anchor intact."""
    messages = _injected_conversation()
    compressor = _compressor(head_keep=4)
    triggered = compressor.should_compress(messages)
    assert triggered is True

    result = await compressor.compress(messages)

    assert _has_summary(result), "compression should actually have fired"
    loc = _find_anchor_block(result)
    assert loc is not None, "memory block lost by compression"
    idx, block = loc
    assert idx == 1, f"memory block drifted to index {idx}"
    assert "Relevant memories" in str(block.content)
    assert "user prefers concise replies" in str(block.content)


@pytest.mark.asyncio
async def test_head_keep_one_still_covers_the_injection_block() -> None:
    """Boundary ``head_keep=1``: the block is the FIRST non-system
    message, so the minimum head still covers it — but the user's task
    message now falls into the summarised middle (current behavior,
    pinned; a head_keep=1 config trades the task away for the anchor)."""
    messages = _injected_conversation()
    result = await _compressor(head_keep=1).compress(messages)

    loc = _find_anchor_block(result)
    assert loc is not None
    assert loc[0] == 1
    task_still_present = any(str(m.content) == "task" for m in result)
    assert not task_still_present, "expected the task message to be summarised away"


@pytest.mark.asyncio
async def test_head_keep_zero_floored_to_one_protects_injection_block() -> None:
    """RT-2 PR-4 (§8.4) — the factory floors ``head_keep`` to 1 while
    per_session recall is active (``floor_head_keep_for_injection``), so the
    effective compressor keeps the ``messages[1]`` anchor even though the
    manifest requested ``head_keep=0``. Compression still fires; the block
    survives at position 1 with its content + anchor intact."""
    effective = floor_head_keep_for_injection(0, per_session_memory_active=True)
    assert effective == 1  # the factory would build the compressor with this

    messages = _injected_conversation()
    result = await _compressor(head_keep=effective).compress(messages)

    assert _has_summary(result), "compression should still have fired"
    loc = _find_anchor_block(result)
    assert loc is not None, "protection failed: memory block lost"
    idx, block = loc
    assert idx == 1
    assert "user prefers concise replies" in str(block.content)


@pytest.mark.asyncio
async def test_head_keep_zero_unprotected_still_loses_block() -> None:
    """A compressor built directly with ``head_keep=0`` (no factory floor —
    e.g. a non-memory agent) still summarises the middle away. This pins
    that the fix lives at construction (the factory floor), not inside the
    compressor, so ``head_keep=0`` stays a valid config where no per_session
    anchor is present."""
    messages = _injected_conversation()
    result = await _compressor(head_keep=0).compress(messages)

    assert _has_summary(result)
    assert _find_anchor_block(result) is None
