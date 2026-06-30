"""Stream CM-12 — mechanical tool-result prune gate.

The cheapest, least-lossy rung of the ``agent_node`` context cascade, run
*before* the CM-2 :class:`~orchestrator.context.working_window.WorkingWindow`.
When the estimated prompt is over threshold it collapses **old** tool results
(every :class:`~langchain_core.messages.ToolMessage` beyond the most-recent
``recent_tool_results_kept``) to a 1-line reference, while leaving every turn
and the assistant's reasoning intact. This is the graceful step between the
window's *drop-the-whole-turn* (which also loses the reasoning) and the L.L2
:class:`~orchestrator.context.compressor.ContextCompressor`'s *LLM summary*
(which costs a model call).

Design anchors (docs/design/tool-result-context-budget.md §Phase 2):

* **Token-gated** — no-op under ``context_window * threshold_pct`` (zero
  behaviour change for runs that are not overflowing). Shares the compressor's
  estimator basis (CM-C6).
* **Count-based recent protection** — the bloat shape is *many tool calls*
  (often within one turn), so the recent window is counted in ``ToolMessage``s,
  not turns: a turn-based window would not relieve a single turn that fired
  eight searches.
* **Pairing-safe by construction** — prune only **rewrites** ``ToolMessage``
  content, never removes a message, so no ``AIMessage.tool_calls`` ↔
  ``ToolMessage`` pair is ever split. No boundary logic needed.
* **Lossless for externalized results** — a result Phase 1 (CM-5) externalized
  carries the ``<tool-result-overflow>`` footer pointing at the full output on
  disk under ``.tool_results/``; pruning it keeps just that footer, so the
  model can ``read_file`` it back. (Bonus: drops the untrusted spotlight-fenced
  preview, improving the trust posture.) A small non-externalized result has no
  on-disk copy, so it collapses to a lossy ``<tool-result-pruned>`` stub —
  still strictly less lossy than the whole-turn drop the window would otherwise
  apply to the same span.
* **Prompt-view only** (CM-C4) — like the window, the gate shapes only the
  message list handed to *this* LLM call; ``agent_node`` returns just the new
  response tail and the ``add_messages`` reducer never deletes, so the
  checkpointed history is never rewritten — the next turn reloads it in full and
  prunes afresh.
* **Idempotent** — a content already reduced to a stub or to a footer-only
  reference is skipped.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, ToolMessage

from helix_agent.runtime.tokens import TokenEstimator
from orchestrator.context.compressor import estimate_tokens
from orchestrator.tools.overflow import OVERFLOW_FOOTER_TAG_OPEN

logger = logging.getLogger(__name__)

#: Wrapper tags on the lossy stub left for a pruned non-externalized result.
#: Doubles as the idempotency marker (a content that already starts with this
#: has been pruned before).
_PRUNE_TAG_OPEN = "<tool-result-pruned>"
_PRUNE_TAG_CLOSE = "</tool-result-pruned>"


@dataclass(frozen=True)
class PruneResult:
    """Outcome of a prune pass — the (possibly rewritten) prompt view plus how
    many tool results were collapsed (``0`` ⇒ no-op)."""

    messages: list[BaseMessage]
    pruned_count: int


def _is_already_pruned(content: str) -> bool:
    """True when ``content`` is already a prune stub or a footer-only reference.

    A *not-yet-pruned* externalized result has its preview body before the
    footer, so it does not start with the overflow tag — only a footer-only
    (already-pruned) message does. Keeps the pass idempotent.
    """
    stripped = content.lstrip()
    return stripped.startswith(_PRUNE_TAG_OPEN) or stripped.startswith(OVERFLOW_FOOTER_TAG_OPEN)


def _prune_tool_message(message: ToolMessage) -> ToolMessage:
    """Collapse one old ``ToolMessage`` to a 1-line reference.

    Returns the message unchanged when it is non-string (multimodal) content or
    already pruned, so the caller's ``pruned_count`` stays accurate.
    """
    content = message.content
    if not isinstance(content, str) or _is_already_pruned(content):
        return message
    footer_at = content.find(OVERFLOW_FOOTER_TAG_OPEN)
    if footer_at != -1:
        # Externalized (CM-5): keep the trusted footer alone — the full output
        # is on disk, so this is lossless and re-readable via read_file.
        new_content = content[footer_at:].lstrip("\n")
    else:
        name = message.name or "tool"
        new_content = (
            f"{_PRUNE_TAG_OPEN}\n"
            f"[{name}] {len(content):,} chars elided (older context, not preserved)\n"
            f"{_PRUNE_TAG_CLOSE}"
        )
    # Preserve tool_call_id (REQUIRED for AIMessage pairing) + name + id. The id
    # is carried so that, even though this prompt view is never persisted, an
    # accidental persistence path would replace-by-id (add_messages) rather than
    # duplicate. ``artifact`` is dropped — it never reaches the LLM.
    return ToolMessage(
        content=new_content,
        tool_call_id=message.tool_call_id,
        name=message.name,
        id=message.id,
    )


def prune_old_tool_results(
    messages: Sequence[BaseMessage], *, recent_tool_results_kept: int
) -> PruneResult:
    """Collapse every ``ToolMessage`` except the most-recent N to a reference.

    Token-unaware: callers gate on size via :meth:`ToolResultPruner.should_prune`.
    Returns a new list — never mutates the input. No-op (``pruned_count == 0``,
    original list returned) when there are at most ``recent_tool_results_kept``
    tool results to begin with.
    """
    msgs = list(messages)
    tool_idxs = [i for i, m in enumerate(msgs) if isinstance(m, ToolMessage)]
    if len(tool_idxs) <= recent_tool_results_kept:
        return PruneResult(messages=msgs, pruned_count=0)
    protected = set(tool_idxs[len(tool_idxs) - recent_tool_results_kept :])
    out: list[BaseMessage] = []
    pruned = 0
    for i, message in enumerate(msgs):
        if isinstance(message, ToolMessage) and i not in protected:
            replaced = _prune_tool_message(message)
            if replaced is not message:
                pruned += 1
            out.append(replaced)
        else:
            out.append(message)
    return PruneResult(messages=out, pruned_count=pruned)


@dataclass(frozen=True)
class ToolResultPruner:
    """Token-gated mechanical prune of old tool results (working memory).

    Build one per agent (the factory wires ``policies.tool_result_prune`` into
    this) and pass to
    :func:`~orchestrator.graph_builder.builder.build_react_graph` so
    ``agent_node`` can call :meth:`apply` at its entry, *before* the CM-2 window.
    """

    context_window: int
    threshold_pct: float = 0.7
    recent_tool_results_kept: int = 4
    #: Stream HX-1 — injected token estimator, shared with the window /
    #: compressor so all gates keep one estimation basis (CM-C6). ``None`` keeps
    #: the legacy ``chars // 4`` heuristic for network-free unit tests.
    estimator: TokenEstimator | None = None

    @property
    def threshold_tokens(self) -> int:
        """Token threshold at/above which a pass prunes. Same shape as the
        window / compressor thresholds so the gates share one basis."""
        return int(self.context_window * self.threshold_pct)

    def should_prune(self, messages: Sequence[BaseMessage]) -> bool:
        """Cheap preflight — ``True`` when the estimate meets/exceeds threshold."""
        return estimate_tokens(messages, estimator=self.estimator) >= self.threshold_tokens

    def apply(self, messages: Sequence[BaseMessage]) -> PruneResult:
        """Prune old tool results when over threshold, else no-op.

        Returns a :class:`PruneResult`; ``pruned_count == 0`` means the prompt
        was left untouched (under threshold, or at/under
        ``recent_tool_results_kept`` tool results).
        """
        msgs = list(messages)
        if not self.should_prune(msgs):
            return PruneResult(messages=msgs, pruned_count=0)
        result = prune_old_tool_results(
            msgs, recent_tool_results_kept=self.recent_tool_results_kept
        )
        if result.pruned_count:
            logger.info(
                "tool_result_prune.pruned count=%d kept=%d",
                result.pruned_count,
                self.recent_tool_results_kept,
            )
        return result
