"""Stream RT-2 PR-1 (RT-ADR-5) — system-message coalescing tests.

Unit-pins :func:`orchestrator.llm.coalesce.coalesce_system_messages`
(merge order, id / additional_kwargs preservation, zero-copy no-op,
promotion when no leading system exists, input immutability) plus the
per-request wiring in the two adapter ``complete()`` entry points: the
OpenAI adapter previously passed a mid-conversation SystemMessage
through at its list position (strict OpenAI-compatible backends reject
that with 400 — deer-flow #3711), while the Anthropic adapter already
hoisted every SystemMessage into the top-level ``system`` field.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from orchestrator.llm import (
    AnthropicProvider,
    OpenAIProvider,
    RecordingAnthropicClient,
    RecordingOpenAIClient,
)
from orchestrator.llm.coalesce import coalesce_system_messages

# ---------------------------------------------------------------------------
# coalesce_system_messages — unit behaviour
# ---------------------------------------------------------------------------


def test_mid_conversation_systems_merge_into_first() -> None:
    sys_a = SystemMessage(content="base prompt", id="sys-a")
    human = HumanMessage(content="hi")
    sys_b = SystemMessage(content="<context-summary>earlier stuff</context-summary>")
    ai = AIMessage(content="ok")
    sys_c = SystemMessage(content="late note")

    out = coalesce_system_messages([sys_a, human, sys_b, ai, sys_c])

    assert [type(m) for m in out] == [SystemMessage, HumanMessage, AIMessage]
    merged = out[0]
    assert merged.content == (
        "base prompt\n\n<context-summary>earlier stuff</context-summary>\n\nlate note"
    )
    # The merged message keeps the FIRST system message's id.
    assert merged.id == "sys-a"
    # Non-system messages ride through untouched, in order.
    assert out[1] is human
    assert out[2] is ai


def test_merged_kwargs_union_first_wins() -> None:
    """The leading system message is authoritative — a later (injected)
    system message contributes new keys only and can never override the
    head's (e.g. a ``expert_work_cache_anchor`` already set there)."""
    sys_a = SystemMessage(content="a", additional_kwargs={"keep": 1, "clash": "first"})
    sys_b = SystemMessage(content="b", additional_kwargs={"clash": "second", "extra": True})

    out = coalesce_system_messages([sys_a, HumanMessage(content="hi"), sys_b])

    assert out[0].additional_kwargs == {"keep": 1, "clash": "first", "extra": True}


def test_single_leading_system_is_zero_copy() -> None:
    msgs = [SystemMessage(content="only"), HumanMessage(content="hi")]
    assert coalesce_system_messages(msgs) is msgs


def test_no_system_is_zero_copy() -> None:
    msgs = [HumanMessage(content="hi"), AIMessage(content="ok")]
    assert coalesce_system_messages(msgs) is msgs


def test_empty_list_is_zero_copy() -> None:
    msgs: list[HumanMessage] = []
    assert coalesce_system_messages(msgs) is msgs


def test_mid_only_system_promoted_to_front() -> None:
    human = HumanMessage(content="hi")
    summary = SystemMessage(content="<context-summary>s</context-summary>", id="sum-1")
    ai = AIMessage(content="ok")

    out = coalesce_system_messages([human, summary, ai])

    assert isinstance(out[0], SystemMessage)
    assert out[0].content == "<context-summary>s</context-summary>"
    assert out[0].id == "sum-1"
    assert out[1] is human
    assert out[2] is ai


def test_input_not_mutated() -> None:
    sys_a = SystemMessage(content="a", additional_kwargs={"k": 1})
    sys_b = SystemMessage(content="b")
    msgs = [sys_a, HumanMessage(content="hi"), sys_b]
    snapshot = list(msgs)

    coalesce_system_messages(msgs)

    assert msgs == snapshot
    assert sys_a.content == "a"
    assert sys_a.additional_kwargs == {"k": 1}
    assert sys_b.content == "b"


def test_block_list_content_flattened() -> None:
    sys_a = SystemMessage(content="lead")
    sys_b = SystemMessage(content=[{"type": "text", "text": "block text"}])

    out = coalesce_system_messages([sys_a, HumanMessage(content="hi"), sys_b])

    assert out[0].content == "lead\n\nblock text"


def test_empty_system_contents_skipped_in_join() -> None:
    sys_a = SystemMessage(content="lead")
    sys_b = SystemMessage(content="")
    sys_c = SystemMessage(content="tail note")

    out = coalesce_system_messages([sys_a, HumanMessage(content="hi"), sys_b, sys_c])

    assert out[0].content == "lead\n\ntail note"


# ---------------------------------------------------------------------------
# Adapter wiring — the one-line complete() coalescing call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_adapter_coalesces_mid_conversation_system() -> None:
    """RT-ADR-5 live-bug fix: the OpenAI adapter (which every
    openai_compatible vendor preset rides) must never emit a
    ``role: system`` entry past position 0."""
    client = RecordingOpenAIClient(response={"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAIProvider(client=client, model="qwen-max")

    await provider.complete(
        messages=[
            SystemMessage(content="base"),
            HumanMessage(content="hi"),
            SystemMessage(content="<context-summary>compressed</context-summary>"),
            AIMessage(content="ack"),
        ],
        tools=[],
    )

    wire = client.calls[0]["messages"]
    system_entries = [m for m in wire if m["role"] == "system"]
    assert len(system_entries) == 1
    assert wire[0]["role"] == "system"
    assert wire[0]["content"] == "base\n\n<context-summary>compressed</context-summary>"
    # The rest of the conversation is intact and in order.
    assert [m["role"] for m in wire] == ["system", "user", "assistant"]


@pytest.mark.asyncio
async def test_anthropic_adapter_keeps_single_system_field() -> None:
    """The Anthropic adapter already hoisted every SystemMessage into
    the top-level ``system`` field; coalescing upstream must keep that
    contract byte-identical (same order, same separator)."""
    client = RecordingAnthropicClient(response={"content": [{"type": "text", "text": "ok"}]})
    provider = AnthropicProvider(client=client, model="claude-x", cache_enabled=False)

    await provider.complete(
        messages=[
            SystemMessage(content="base"),
            HumanMessage(content="hi"),
            SystemMessage(content="<context-summary>compressed</context-summary>"),
        ],
        tools=[],
    )

    call = client.calls[0]
    assert call["system"] == "base\n\n<context-summary>compressed</context-summary>"
    assert all(m["role"] != "system" for m in call["messages"])
