"""Resume sanitisation tests — Stream 15.3 test-debt closeout.

Covers the orphan tool-call repair (E.15) directly: the pure
:func:`sanitize_dangling_tool_calls` over its edge cases (the reassessment
flagged it as code+wired but thinly tested — including the cross-recursion
case where a cancel deep in a subagent leaves dangling calls at several
positions in the message list), plus the :meth:`GraphRunner.sanitize_thread`
wrapper that writes the placeholders back to the checkpoint.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from orchestrator.resume import PLACEHOLDER_CONTENT, sanitize_dangling_tool_calls
from orchestrator.runner import GraphRunner


def _ai_with_calls(*ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": i, "name": "do_thing", "args": {}} for i in ids],
    )


# --------------------------------------------------------------------------
# pure function — sanitize_dangling_tool_calls
# --------------------------------------------------------------------------


def test_empty_history_yields_nothing() -> None:
    assert sanitize_dangling_tool_calls([]) == []


def test_no_aimessage_yields_nothing() -> None:
    assert sanitize_dangling_tool_calls([HumanMessage(content="hi")]) == []


def test_fully_answered_history_yields_nothing() -> None:
    messages = [
        _ai_with_calls("call_1"),
        ToolMessage(content="ok", tool_call_id="call_1"),
    ]
    assert sanitize_dangling_tool_calls(messages) == []


def test_single_dangling_call_gets_one_placeholder() -> None:
    placeholders = sanitize_dangling_tool_calls([_ai_with_calls("call_1")])
    assert len(placeholders) == 1
    ph = placeholders[0]
    assert ph.tool_call_id == "call_1"
    assert ph.content == PLACEHOLDER_CONTENT
    assert ph.status == "error"


def test_multiple_dangling_preserve_first_seen_order() -> None:
    placeholders = sanitize_dangling_tool_calls([_ai_with_calls("a", "b", "c")])
    assert [p.tool_call_id for p in placeholders] == ["a", "b", "c"]


def test_only_unanswered_calls_get_placeholders() -> None:
    messages = [
        _ai_with_calls("answered", "dangling"),
        ToolMessage(content="ok", tool_call_id="answered"),
    ]
    placeholders = sanitize_dangling_tool_calls(messages)
    assert [p.tool_call_id for p in placeholders] == ["dangling"]


def test_duplicate_ids_yield_single_placeholder() -> None:
    messages = [_ai_with_calls("dup"), _ai_with_calls("dup")]
    placeholders = sanitize_dangling_tool_calls(messages)
    assert [p.tool_call_id for p in placeholders] == ["dup"]


def test_empty_tool_call_id_is_skipped() -> None:
    # A tool call with no id can't be answered by id — skip rather than emit
    # a placeholder with an empty tool_call_id (which would stay dangling).
    assert sanitize_dangling_tool_calls([_ai_with_calls("")]) == []


def test_cross_recursion_dangling_across_multiple_aimessages() -> None:
    """A cancel deep in a subagent recursion leaves dangling calls at several
    list positions — every unanswered call across all AIMessages is repaired,
    answered ones in between are left alone."""
    messages = [
        HumanMessage(content="start"),
        _ai_with_calls("outer_1"),
        ToolMessage(content="ok", tool_call_id="outer_1"),
        _ai_with_calls("inner_1", "inner_2"),  # subagent turn, cancelled mid-dispatch
        ToolMessage(content="ok", tool_call_id="inner_1"),
        _ai_with_calls("outer_2"),  # back at the parent, also dangling
    ]
    placeholders = sanitize_dangling_tool_calls(messages)
    assert [p.tool_call_id for p in placeholders] == ["inner_2", "outer_2"]
    assert all(p.status == "error" for p in placeholders)


# --------------------------------------------------------------------------
# GraphRunner.sanitize_thread — write-back wrapper
# --------------------------------------------------------------------------


class _Snapshot:
    def __init__(self, values: Any) -> None:
        self.values = values


class _FakeGraph:
    """Records ``aupdate_state`` calls; returns a fixed ``aget_state`` snapshot."""

    def __init__(self, values: Any) -> None:
        self._values = values
        self.updates: list[tuple[dict[str, Any], str | None]] = []

    async def aget_state(self, _config: Any) -> _Snapshot:
        return _Snapshot(self._values)

    async def aupdate_state(self, _config: Any, values: dict[str, Any], *, as_node: str) -> None:
        self.updates.append((values, as_node))


@pytest.mark.asyncio
async def test_sanitize_thread_injects_placeholders_as_tools_node() -> None:
    runner = GraphRunner(checkpointer=InMemorySaver())
    graph = _FakeGraph({"messages": [_ai_with_calls("call_1", "call_2")]})

    count = await runner.sanitize_thread(graph, {"configurable": {"thread_id": "t1"}})  # type: ignore[arg-type]

    assert count == 2
    assert len(graph.updates) == 1
    values, as_node = graph.updates[0]
    assert as_node == "tools"
    assert [m.tool_call_id for m in values["messages"]] == ["call_1", "call_2"]


@pytest.mark.asyncio
async def test_sanitize_thread_noop_on_valid_history() -> None:
    runner = GraphRunner(checkpointer=InMemorySaver())
    graph = _FakeGraph(
        {"messages": [_ai_with_calls("call_1"), ToolMessage(content="ok", tool_call_id="call_1")]}
    )

    count = await runner.sanitize_thread(graph, {"configurable": {"thread_id": "t1"}})  # type: ignore[arg-type]

    assert count == 0
    assert graph.updates == []


@pytest.mark.asyncio
async def test_sanitize_thread_handles_fresh_thread_without_checkpoint() -> None:
    runner = GraphRunner(checkpointer=InMemorySaver())
    # A fresh thread's snapshot has no dict values (no checkpoint yet).
    graph = _FakeGraph(None)

    count = await runner.sanitize_thread(graph, {"configurable": {"thread_id": "t1"}})  # type: ignore[arg-type]

    assert count == 0
    assert graph.updates == []
