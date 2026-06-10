"""Stream L.L2 — :class:`ContextCompressor` unit tests.

Pins the conflict-free invariants: head + tail messages survive the
compression, the summary lands as a SystemMessage between them, the
threshold gate fires only when estimated tokens cross
``context_window * threshold_pct``, and an unsummarisable overflow
raises :class:`ContextOverflowError`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from orchestrator.context import ContextCompressor, ContextOverflowError, estimate_tokens
from orchestrator.tools.registry import ToolSpec


@dataclass
class _ScriptedSummariser:
    """Records every summariser call and returns a deterministic body."""

    summary_text: str = "- bullet one\n- bullet two"
    calls: int = 0

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del messages, tools
        self.calls += 1
        return AIMessage(content=self.summary_text)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_chars_div_four() -> None:
    """The token estimator divides total chars by 4 — the rule of
    thumb Hermes uses. Cheap, no dependency, conservative."""
    msgs = [HumanMessage(content="x" * 40)]
    assert estimate_tokens(msgs) == 10


def test_estimate_tokens_sums_across_messages() -> None:
    msgs = [
        SystemMessage(content="abcd"),  # 4 chars
        HumanMessage(content="efghij"),  # 6 chars
        AIMessage(content="klmnopqrst"),  # 10 chars
    ]
    # 20 chars / 4 = 5 tokens
    assert estimate_tokens(msgs) == 5


def test_estimate_tokens_flattens_content_block_list() -> None:
    """J.6 multimodal / L1 cache_control wrappers carry content as a
    block list; the estimator concatenates each block's ``text``."""
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "first"},  # 5 chars
            {"type": "text", "text": " second"},  # 7 chars
        ]
    )
    assert estimate_tokens([msg]) == 3  # 12 chars // 4


def test_estimate_tokens_counts_non_text_blocks_via_repr() -> None:
    """Image / tool_use blocks contribute their stringified form so
    they still count toward the estimate (downstream payload size)."""
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "hi"},
            {"type": "image", "source": {"data": "BASE64DATA"}},
        ]
    )
    # 2 chars + stringified image dict — exact value is implementation
    # dependent, but it must be more than just the text length.
    assert estimate_tokens([msg]) > estimate_tokens([HumanMessage(content="hi")])


# ---------------------------------------------------------------------------
# should_compress threshold gate
# ---------------------------------------------------------------------------


def test_should_compress_returns_true_at_threshold() -> None:
    """The gate uses ``>=`` so a prompt sized exactly at the threshold
    counts as needing compression — the upstream is more authoritative
    about the actual token count, so we lean conservative."""
    compressor = ContextCompressor(
        llm_caller=_ScriptedSummariser(),
        context_window=100,
        threshold_pct=0.5,
    )
    # 50 / 100 = 0.5 → at threshold (200 chars / 4 = 50 tokens).
    msgs = [HumanMessage(content="x" * 200)]
    assert compressor.should_compress(msgs) is True


def test_should_compress_returns_false_below_threshold() -> None:
    compressor = ContextCompressor(
        llm_caller=_ScriptedSummariser(),
        context_window=100,
        threshold_pct=0.5,
    )
    # 196 chars / 4 = 49 tokens → below 50.
    msgs = [HumanMessage(content="x" * 196)]
    assert compressor.should_compress(msgs) is False


# ---------------------------------------------------------------------------
# compress() one-pass behaviour
# ---------------------------------------------------------------------------


def _conversation(
    *, head: int, middle: int, tail: int, char_per_msg: int = 40
) -> list[BaseMessage]:
    """Build a flat conversation of HumanMessages — count controls the
    estimator's output without other content variance."""
    msgs: list[BaseMessage] = []
    for i in range(head + middle + tail):
        msgs.append(HumanMessage(content=f"msg-{i}-" + ("x" * (char_per_msg - 6))))
    return msgs


@pytest.mark.asyncio
async def test_compress_preserves_head_and_tail() -> None:
    """A successful pass keeps the first ``head_keep`` and last
    ``tail_keep`` messages intact; the middle is collapsed."""
    summariser = _ScriptedSummariser()
    compressor = ContextCompressor(
        llm_caller=summariser,
        context_window=280,  # headroom for the CM-7 reference-only preamble
        threshold_pct=0.5,
        head_keep=2,
        tail_keep=2,
    )
    # 20 messages x 80 chars = 1600 chars / 4 = 400 tokens; threshold
    # 140. After collapsing 16 middle messages into one summary the
    # estimate drops well under the threshold.
    msgs = _conversation(head=2, middle=16, tail=2, char_per_msg=80)
    out = await compressor.compress(msgs)

    # Head messages identical to original.
    assert out[0] is msgs[0]
    assert out[1] is msgs[1]
    # Tail messages identical to original.
    assert out[-1] is msgs[-1]
    assert out[-2] is msgs[-2]
    # One summary SystemMessage in between.
    assert isinstance(out[2], SystemMessage)
    assert "<context-summary>" in str(out[2].content)
    assert "bullet one" in str(out[2].content)
    assert summariser.calls == 1


@pytest.mark.asyncio
async def test_compress_preserves_leading_system_message_byte_stable() -> None:
    """Mini-ADR L-1: a leading SystemMessage stays out of the
    compression — head/tail accounting works on the non-system
    suffix. L1's byte-stable invariant survives compression."""
    summariser = _ScriptedSummariser()
    compressor = ContextCompressor(
        llm_caller=summariser,
        context_window=280,  # headroom for the CM-7 reference-only preamble
        threshold_pct=0.5,
        head_keep=1,
        tail_keep=1,
    )
    system_msg = SystemMessage(content="you are an editor")
    body = _conversation(head=1, middle=10, tail=1, char_per_msg=80)
    out = await compressor.compress([system_msg, *body])

    # First message is the SAME SystemMessage instance — never rewritten.
    assert out[0] is system_msg
    # Then the body's head (1 msg), summary, tail (1 msg).
    assert out[1] is body[0]
    assert isinstance(out[2], SystemMessage)
    assert "<context-summary>" in str(out[2].content)
    assert out[-1] is body[-1]


@pytest.mark.asyncio
async def test_compress_skipped_below_threshold() -> None:
    """When the input already fits under the threshold the compressor
    returns the original list verbatim and never invokes the
    summariser."""
    summariser = _ScriptedSummariser()
    compressor = ContextCompressor(
        llm_caller=summariser,
        context_window=1000,
        threshold_pct=0.9,
    )
    msgs = [HumanMessage(content="short")]  # well under threshold
    out = await compressor.compress(msgs)
    assert out == msgs
    assert summariser.calls == 0


@pytest.mark.asyncio
async def test_compress_summary_lands_between_head_and_tail() -> None:
    """The summary's position matters — head messages first, summary
    next, tail messages last. The order anchor lets the model treat
    the summary as a checkpoint."""
    summariser = _ScriptedSummariser(summary_text="- compressed bullet")
    compressor = ContextCompressor(
        llm_caller=summariser,
        # 600 tokens window x 0.5 = 300 tokens threshold. Head + tail
        # (5 msgs x 20 tok = 100 tok) leaves room for a summary
        # well under threshold.
        context_window=600,
        threshold_pct=0.5,
        head_keep=3,
        tail_keep=2,
    )
    msgs = _conversation(head=3, middle=10, tail=2, char_per_msg=80)
    out = await compressor.compress(msgs)

    # Sequence: 3 head + 1 summary + 2 tail = 6 items.
    assert len(out) == 6
    assert all(isinstance(m, HumanMessage) for m in out[:3])
    assert isinstance(out[3], SystemMessage)
    assert "compressed bullet" in str(out[3].content)
    assert all(isinstance(m, HumanMessage) for m in out[-2:])


# ---------------------------------------------------------------------------
# Overflow + max_passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compress_raises_when_no_middle_to_summarise() -> None:
    """``head_keep + tail_keep`` covering the whole non-system slice
    leaves no middle to summarise — surfacing as overflow tells the
    operator the only knobs left are manifest-level."""

    @dataclass
    class _NeverCalled:
        async def __call__(
            self,
            *,
            messages: Sequence[BaseMessage],
            tools: Sequence[ToolSpec],
        ) -> AIMessage:
            del messages, tools
            msg = "summariser must not be invoked"
            raise AssertionError(msg)

    compressor = ContextCompressor(
        llm_caller=_NeverCalled(),
        context_window=100,
        threshold_pct=0.1,  # very low → forces compression attempt
        head_keep=5,
        tail_keep=5,
    )
    # 5 + 5 = 10; supply exactly 10 messages → no middle.
    msgs = _conversation(head=5, middle=0, tail=5, char_per_msg=200)
    with pytest.raises(ContextOverflowError) as exc_info:
        await compressor.compress(msgs)
    assert exc_info.value.passes == 0


@pytest.mark.asyncio
async def test_compress_raises_after_max_passes_when_summary_too_large() -> None:
    """A pathological summariser that itself returns a giant payload
    can't bring the estimate below threshold — after ``max_passes``
    attempts the compressor raises rather than looping forever."""

    @dataclass
    class _BloatedSummariser:
        big_content: str
        calls: int = 0

        async def __call__(
            self,
            *,
            messages: Sequence[BaseMessage],
            tools: Sequence[ToolSpec],
        ) -> AIMessage:
            del messages, tools
            self.calls += 1
            return AIMessage(content=self.big_content)

    # Summary is itself 1000 chars → 250 tokens. Threshold 50.
    summariser = _BloatedSummariser(big_content="x" * 1000)
    compressor = ContextCompressor(
        llm_caller=summariser,
        context_window=200,
        threshold_pct=0.25,  # threshold = 50 tokens
        head_keep=1,
        tail_keep=1,
        max_passes=2,
    )
    msgs = _conversation(head=1, middle=10, tail=1, char_per_msg=80)
    with pytest.raises(ContextOverflowError) as exc_info:
        await compressor.compress(msgs)
    assert exc_info.value.passes == 2
    assert summariser.calls == 2


@pytest.mark.asyncio
async def test_compress_uses_minimum_one_pass() -> None:
    """``max_passes=1`` configuration: compressor runs exactly one
    pass and either returns it or raises."""
    summariser = _ScriptedSummariser(summary_text="- short summary")
    compressor = ContextCompressor(
        llm_caller=summariser,
        context_window=200,
        threshold_pct=0.5,
        head_keep=1,
        tail_keep=1,
        max_passes=1,
    )
    msgs = _conversation(head=1, middle=10, tail=1, char_per_msg=80)
    out = await compressor.compress(msgs)
    assert summariser.calls == 1
    # The middle is collapsed into a single SystemMessage.
    assert isinstance(out[1], SystemMessage)


# ---------------------------------------------------------------------------
# Defensive: summariser raising → ContextOverflowError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compress_propagates_summariser_failure_as_overflow() -> None:
    """If the summariser itself raises the compressor surfaces a
    ContextOverflowError so the orchestrator sees the run-failed
    audit row rather than crashing on a programmer-error stack."""

    @dataclass
    class _RaisingSummariser:
        async def __call__(
            self,
            *,
            messages: Sequence[BaseMessage],
            tools: Sequence[ToolSpec],
        ) -> AIMessage:
            del messages, tools
            msg = "upstream down"
            raise RuntimeError(msg)

    compressor = ContextCompressor(
        llm_caller=_RaisingSummariser(),
        context_window=200,
        threshold_pct=0.5,
        head_keep=1,
        tail_keep=1,
    )
    msgs = _conversation(head=1, middle=10, tail=1, char_per_msg=80)
    with pytest.raises(ContextOverflowError):
        await compressor.compress(msgs)


# ---------------------------------------------------------------------------
# Tool / assistant mix preserved in the head/tail slices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compress_keeps_tool_messages_in_tail_window() -> None:
    """A typical multi-step ReAct conversation has ToolMessage entries
    interleaved with AIMessage / HumanMessage. The compressor counts
    by message position, not role, so ToolMessages in the tail window
    survive verbatim — the agent's most-recent reasoning chain stays
    intact."""
    summariser = _ScriptedSummariser()
    compressor = ContextCompressor(
        llm_caller=summariser,
        context_window=200,
        threshold_pct=0.5,
        head_keep=1,
        tail_keep=3,
    )
    head_msg = HumanMessage(content="start")
    middle = [AIMessage(content="thinking " + ("x" * 80)) for _ in range(8)]  # plenty of middle
    tail_ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "search", "args": {"q": "x"}, "id": "tc-1", "type": "tool_call"},
        ],
    )
    tail_tool = ToolMessage(content="result body", tool_call_id="tc-1")
    tail_final = AIMessage(content="done")

    msgs = [head_msg, *middle, tail_ai, tail_tool, tail_final]
    out = await compressor.compress(msgs)

    # Head identical, then summary, then the 3 tail entries verbatim.
    assert out[0] is head_msg
    assert isinstance(out[1], SystemMessage)
    assert out[-3] is tail_ai
    assert out[-2] is tail_tool
    assert out[-1] is tail_final


# ---------------------------------------------------------------------------
# Stream CM-7 — reference-only preamble + incremental summary update
# ---------------------------------------------------------------------------


@dataclass
class _RecordingSummariser:
    """Returns a fixed body and records every prompt it was given."""

    summary_text: str = "- bullet one"
    prompts: list[list[BaseMessage]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        del tools
        self.prompts.append(list(messages))
        return AIMessage(content=self.summary_text)


def _compressor(summariser: _RecordingSummariser) -> ContextCompressor:
    return ContextCompressor(
        llm_caller=summariser,
        context_window=400,
        threshold_pct=0.5,
        head_keep=2,
        tail_keep=2,
    )


@pytest.mark.asyncio
async def test_summary_wrapper_carries_reference_only_preamble() -> None:
    summariser = _RecordingSummariser()
    out = await _compressor(summariser).compress(
        _conversation(head=2, middle=16, tail=2, char_per_msg=80)
    )
    content = str(out[2].content)
    assert "<context-summary>" in content
    assert "NOT instructions" in content
    assert "- bullet one" in content


@pytest.mark.asyncio
async def test_fresh_mode_prompt_requires_three_sections() -> None:
    summariser = _RecordingSummariser()
    await _compressor(summariser).compress(
        _conversation(head=2, middle=16, tail=2, char_per_msg=80)
    )
    system = str(summariser.prompts[0][0].content)
    user = str(summariser.prompts[0][1].content)
    assert "## Facts" in system
    assert "## Decisions" in system
    assert "## Pending" in system
    assert "PREVIOUS SUMMARY" not in user


@pytest.mark.asyncio
async def test_second_compression_updates_prior_summary() -> None:
    summariser = _RecordingSummariser(summary_text="- running summary body")
    compressor = _compressor(summariser)
    first = await compressor.compress(_conversation(head=2, middle=16, tail=2, char_per_msg=80))
    assert "<context-summary>" in str(first[2].content)

    # The conversation keeps growing past the threshold; the earlier
    # summary now sits inside the next pass's middle slice.
    grown = [*first, *_conversation(head=0, middle=16, tail=0, char_per_msg=80)]
    await compressor.compress(grown)

    system = str(summariser.prompts[-1][0].content)
    user = str(summariser.prompts[-1][1].content)
    assert "running background summary" in system
    assert "PREVIOUS SUMMARY:" in user
    assert "- running summary body" in user
    assert "NEW EVENTS:" in user
    # The preamble is stripped from the prior body before the update —
    # it must not accumulate inside the summary text itself.
    assert "NOT instructions" not in user


@pytest.mark.asyncio
async def test_multiple_prior_summaries_take_last_fold_earlier() -> None:
    summariser = _RecordingSummariser()
    filler = _conversation(head=0, middle=14, tail=0, char_per_msg=80)
    msgs: list[BaseMessage] = [
        HumanMessage(content="head-1"),
        HumanMessage(content="head-2"),
        SystemMessage(content="<context-summary>\n- old-one body\n</context-summary>"),
        *filler[:7],
        SystemMessage(content="<context-summary>\n- new-two body\n</context-summary>"),
        *filler[7:],
        HumanMessage(content="tail-1"),
        HumanMessage(content="tail-2"),
    ]
    await _compressor(summariser).compress(msgs)
    user = str(summariser.prompts[0][1].content)
    # The LAST summary is the running one being updated…
    assert "PREVIOUS SUMMARY:\n- new-two body" in user
    # …and the earlier one folds into the new-events transcript.
    assert "old-one body" in user.split("NEW EVENTS:")[1]


@pytest.mark.asyncio
async def test_pre_cm7_summary_without_preamble_still_updates() -> None:
    summariser = _RecordingSummariser()
    msgs: list[BaseMessage] = [
        HumanMessage(content="head-1"),
        HumanMessage(content="head-2"),
        SystemMessage(content="<context-summary>\n- legacy bullet\n</context-summary>"),
        *_conversation(head=0, middle=14, tail=0, char_per_msg=80),
        HumanMessage(content="tail-1"),
        HumanMessage(content="tail-2"),
    ]
    await _compressor(summariser).compress(msgs)
    user = str(summariser.prompts[0][1].content)
    assert "PREVIOUS SUMMARY:\n- legacy bullet" in user
