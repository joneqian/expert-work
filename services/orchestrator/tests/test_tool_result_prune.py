"""Stream CM-12 — mechanical tool-result prune gate.

The gate collapses OLD tool results (beyond the most-recent N) to 1-line
references when the prompt is over threshold: lossless for Phase-1-externalized
results (the ``<tool-result-overflow>`` footer points at the on-disk copy), a
short ``<tool-result-pruned>`` stub otherwise. It only rewrites ToolMessage
content — never removes a message — so tool-call pairing is preserved, and it is
prompt-view only (no checkpoint rewrite, tested at the graph level elsewhere).

All tests use the legacy ``chars // 4`` estimator (``estimator=None``) with a
tiny ``context_window=100`` (threshold 70 tokens) so a few KB of tool content
crosses the gate without any network call.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from orchestrator.context import PruneResult, ToolResultPruner, prune_old_tool_results
from orchestrator.tools.overflow import OVERFLOW_FOOTER_TAG_OPEN, render_overflow_footer

#: ~1000 tokens via chars // 4 — one big result alone clears the 70-token gate.
_BIG = "y" * 4000


def _ai_call(call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "web_search", "args": {}, "id": call_id, "type": "tool_call"}],
    )


def _tool(content: object, *, call_id: str, name: str = "web_search") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name=name)


def _trace(n_searches: int, *, content: str = _BIG) -> list[BaseMessage]:
    """A normal ReAct trace: HumanMessage then N (AIMessage tool_call, ToolMessage)."""
    msgs: list[BaseMessage] = [HumanMessage(content="go")]
    for i in range(n_searches):
        cid = f"tc-{i}"
        msgs.append(_ai_call(cid))
        msgs.append(_tool(content, call_id=cid))
    return msgs


def _pruner(*, kept: int = 2) -> ToolResultPruner:
    return ToolResultPruner(context_window=100, recent_tool_results_kept=kept)


def _tools(messages: list[BaseMessage]) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage)]


def test_under_threshold_is_noop() -> None:
    # Tiny tool contents stay under the 70-token gate ⇒ untouched.
    msgs = _trace(6, content="small")
    res = _pruner().apply(msgs)
    assert res.pruned_count == 0
    assert res.messages == msgs


def test_over_threshold_collapses_old_keeps_recent_full() -> None:
    msgs = _trace(6)
    res = _pruner(kept=2).apply(msgs)
    assert res.pruned_count == 4  # 6 results minus 2 protected
    tools = _tools(res.messages)
    assert len(tools) == 6  # nothing removed
    # The most-recent 2 stay byte-identical.
    assert tools[-1].content == _BIG
    assert tools[-2].content == _BIG
    # The older 4 lost the blob and carry a reference instead.
    for tm in tools[:4]:
        assert _BIG not in str(tm.content)
        assert "chars elided" in str(tm.content)


def test_externalized_result_pruned_to_footer_only_lossless() -> None:
    # A Phase-1 (CM-5) externalized ToolMessage = spotlight-fenced preview + the
    # trusted footer. Pruning keeps the footer alone — the full output is on disk.
    rel = ".tool_results/run-abc/tc-0-web_search.txt"
    footer = render_overflow_footer(rel=rel, total_chars=50_000)
    content = ("PREVIEW-BODY " * 400) + footer
    msgs: list[BaseMessage] = [
        HumanMessage(content="go"),
        _ai_call("tc-0"),
        _tool(content, call_id="tc-0"),
        _ai_call("tc-1"),
        _tool(_BIG, call_id="tc-1"),
    ]
    res = _pruner(kept=1).apply(msgs)
    assert res.pruned_count == 1
    pruned = _tools(res.messages)[0]
    body = str(pruned.content)
    assert body.startswith(OVERFLOW_FOOTER_TAG_OPEN)  # footer-only
    assert rel in body  # still points at the on-disk full output
    assert "PREVIEW-BODY" not in body  # untrusted preview dropped


def test_nonexternalized_stub_carries_tool_name_and_size() -> None:
    msgs = _trace(3)
    res = _pruner(kept=1).apply(msgs)
    oldest = _tools(res.messages)[0]
    body = str(oldest.content)
    assert "<tool-result-pruned>" in body
    assert "[web_search]" in body
    assert f"{len(_BIG):,} chars" in body


def test_tool_call_pairing_preserved() -> None:
    msgs = _trace(5)
    res = _pruner(kept=2).apply(msgs)
    assert len(res.messages) == len(msgs)
    ai_ids = {tc["id"] for m in res.messages if isinstance(m, AIMessage) for tc in m.tool_calls}
    tool_ids = {m.tool_call_id for m in _tools(res.messages)}
    assert ai_ids == tool_ids  # every call still has its result
    # Pruned copies keep their tool_call_id / name so the wire pairing holds.
    for tm in _tools(res.messages):
        assert tm.tool_call_id.startswith("tc-")
        assert tm.name == "web_search"


def test_idempotent() -> None:
    pruner = _pruner(kept=2)
    once = pruner.apply(_trace(6))
    twice = pruner.apply(once.messages)
    assert once.pruned_count == 4
    assert twice.pruned_count == 0  # already-pruned + protected are skipped
    assert [str(m.content) for m in twice.messages] == [str(m.content) for m in once.messages]


def test_at_or_under_kept_is_noop_even_over_threshold() -> None:
    # 2 big results clear the gate, but with kept=2 there is nothing older to prune.
    msgs = _trace(2)
    res = _pruner(kept=2).apply(msgs)
    assert res.pruned_count == 0
    assert all(str(m.content) == _BIG for m in _tools(res.messages))


def test_multimodal_list_content_skipped() -> None:
    # A ToolMessage with list content (e.g. ask_image) is left untouched.
    msgs: list[BaseMessage] = [
        HumanMessage(content="go"),
        _ai_call("tc-0"),
        _tool([{"type": "text", "text": "x" * 4000}], call_id="tc-0", name="ask_image"),
        _ai_call("tc-1"),
        _tool(_BIG, call_id="tc-1"),
        _ai_call("tc-2"),
        _tool(_BIG, call_id="tc-2"),
    ]
    res = _pruner(kept=2).apply(msgs)
    # Only the multimodal result is beyond the recent window; it is skipped.
    assert res.pruned_count == 0
    multimodal = [m for m in _tools(res.messages) if isinstance(m.content, list)]
    assert len(multimodal) == 1


def test_non_tool_messages_untouched() -> None:
    msgs = _trace(6)
    res = _pruner(kept=2).apply(msgs)
    humans = [m for m in res.messages if isinstance(m, HumanMessage)]
    ais = [m for m in res.messages if isinstance(m, AIMessage)]
    assert len(humans) == 1
    assert len(ais) == 6  # every AIMessage tool-call turn intact


def test_pure_function_returns_prune_result() -> None:
    res = prune_old_tool_results(_trace(4), recent_tool_results_kept=1)
    assert isinstance(res, PruneResult)
    assert res.pruned_count == 3
