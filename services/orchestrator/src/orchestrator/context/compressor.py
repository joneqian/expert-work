"""Stream L.L2 — :class:`ContextCompressor` + token preflight.

Implements Hermes ``context_compressor.py:454-600``'s "summarise the
middle" pattern, scoped down to a single-pass-per-turn API. The
:func:`agent_node` preflight calls :func:`ContextCompressor.compress`
when the estimated outbound prompt size exceeds
``context_window * threshold_pct``; the compressor preserves the
first ``head_keep`` and last ``tail_keep`` non-system messages and
collapses the middle into a single ``<context-summary>`` system
message generated via an LLM call.

Mini-ADR L-2 highlights:

* **One-shot per turn** — we don't keep a running summary across
  compressions (Hermes "iterative summary preservation"). Each
  compression starts fresh from the current message list; if the
  conversation grows large enough to need compression repeatedly the
  individual passes are cheap, and the result is easier to reason
  about than a self-feeding summary.
* **Independent summariser LLM call** — the compressor takes its own
  :class:`LLMCaller`. The agent's main router may go through the
  same caller, but the contract is "summarise this, return one
  message" rather than "act as the agent" so a future hop to a
  dedicated cheaper model is a one-field change.
* **Hard cap via ``max_passes``** — if successive summarisations
  cannot bring the estimated size below threshold the compressor
  raises :class:`ContextOverflowError`. Hiding the failure behind a
  silent fallback would let the run keep ballooning until the
  upstream rejects it; the explicit signal lets the orchestrator log
  a clean run-failed audit.
* **Rough char-based estimator** — ``estimate_tokens`` returns
  ``total_chars // 4``. Cheaper than tiktoken (no dependency, no
  per-message tokeniser call) and Hermes uses the same rule of
  thumb. The 4-chars-per-token heuristic is conservative for English
  / code, slightly aggressive for CJK; the threshold ratio gives us
  enough headroom to absorb the difference without ratcheting up
  cost.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from orchestrator.llm.caller import LLMCaller

logger = logging.getLogger(__name__)


#: Stream L.L2 — chars-per-token rule of thumb. The summariser prompt
#: itself is bounded so a fudge here only affects when compression
#: triggers, not what it produces.
_CHARS_PER_TOKEN: int = 4

#: Stream L.L2 — wrapping tags on the summary content so the agent can
#: see at a glance that the middle of its conversation was compressed.
_SUMMARY_TAG_OPEN: str = "<context-summary>"
_SUMMARY_TAG_CLOSE: str = "</context-summary>"

_SUMMARISER_SYSTEM_PROMPT: str = (
    "You are a context compressor. Summarise the conversation excerpt "
    "below into 3-7 short bullet points capturing the essential facts, "
    "decisions, and pending work items. Preserve specific names, paths, "
    "and numerical values verbatim. Do not include any tool-call syntax "
    "or speculation about future steps."
)


class ContextOverflowError(Exception):
    """Stream L.L2 — repeated compression could not get the estimated
    prompt size under the configured threshold.

    Raised by :meth:`ContextCompressor.compress` after ``max_passes``
    attempts. The orchestrator catches it at the
    :class:`MaxStepsExceededError`-style terminal path so the run
    fails with a clean ``RUN_FAILED`` audit row instead of letting
    the upstream provider reject the request with a 422 (Mini-ADR
    L-2 — no silent fallback that hides the overflow).
    """

    def __init__(self, estimated_tokens: int, threshold: int, passes: int) -> None:
        super().__init__(
            f"context overflow: estimated {estimated_tokens} tokens > threshold "
            f"{threshold} after {passes} compression pass(es)"
        )
        self.estimated_tokens = estimated_tokens
        self.threshold = threshold
        self.passes = passes


def estimate_tokens(messages: Sequence[BaseMessage]) -> int:
    """Rough token estimate — ``total_chars // 4``.

    Matches Hermes ``estimate_request_tokens_rough``. Cheap, no
    external dependency, slightly conservative for English and code,
    slightly aggressive for CJK. Adequate for triggering compression;
    upstream still authoritative on the actual count.
    """
    total = 0
    for msg in messages:
        total += len(_message_to_text(msg))
    return total // _CHARS_PER_TOKEN


def _message_to_text(msg: BaseMessage) -> str:
    """Flatten a message to a single text representation.

    Block-list content (J.6 multimodal, L1 cache_control wrappers) is
    folded by concatenating each block's ``text`` field; non-text
    blocks (images, tool_use) contribute their string representation
    so they still count toward the token estimate.
    """
    content = msg.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
            else:
                # Tool-use / image / other → coarse repr keeps the
                # estimate honest even when the actual bytes are
                # downstream-owned (base64 etc.).
                parts.append(str(block))
    return "".join(parts)


@dataclass(frozen=True)
class _SplitMessages:
    """Slice of the message list — head/middle/tail by index, plus
    the leading SystemMessages kept verbatim."""

    leading_systems: list[BaseMessage]
    head: list[BaseMessage]
    middle: list[BaseMessage]
    tail: list[BaseMessage]


def _split(messages: Sequence[BaseMessage], *, head_keep: int, tail_keep: int) -> _SplitMessages:
    """Split ``messages`` into (leading systems, head, middle, tail).

    Leading :class:`SystemMessage` instances stay outside the
    head/tail accounting — the L1 invariant requires the system
    prompt block to stay byte-stable, so the compressor never touches
    it. Head / tail are slices of the *non-system* tail of the list.
    """
    leading_systems: list[BaseMessage] = []
    cursor = 0
    while cursor < len(messages) and isinstance(messages[cursor], SystemMessage):
        leading_systems.append(messages[cursor])
        cursor += 1
    remainder = list(messages[cursor:])
    head = remainder[:head_keep]
    tail = remainder[-tail_keep:] if tail_keep else []
    # Compute middle by index to handle overlap between head/tail when
    # the list is short (head_keep + tail_keep > len(remainder)).
    middle_start = head_keep
    middle_end = len(remainder) - tail_keep if tail_keep else len(remainder)
    if middle_end <= middle_start:
        middle: list[BaseMessage] = []
    else:
        middle = remainder[middle_start:middle_end]
    return _SplitMessages(
        leading_systems=leading_systems,
        head=head,
        middle=middle,
        tail=tail,
    )


def _format_middle_for_summary(middle: Sequence[BaseMessage]) -> str:
    """Render the middle slice as a flat transcript the summariser
    LLM consumes. The format is intentionally simple — role: text —
    so the summariser doesn't get sidetracked by JSON wire format."""
    lines: list[str] = []
    for msg in middle:
        role = _role_label(msg)
        text = _message_to_text(msg).strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


def _role_label(msg: BaseMessage) -> str:
    if isinstance(msg, SystemMessage):
        return "system"
    if isinstance(msg, HumanMessage):
        return "user"
    if isinstance(msg, AIMessage):
        return "assistant"
    if isinstance(msg, ToolMessage):
        return "tool"
    return type(msg).__name__


@dataclass(frozen=True)
class ContextCompressor:
    """Summarise the middle of a conversation when its estimated size
    exceeds the model's context window threshold.

    Build one per agent (the factory wires the manifest's
    ``policies.context_compression`` policy into this). Pass to
    :func:`build_react_graph` so ``agent_node`` can call
    :meth:`compress` at its entry preflight.
    """

    llm_caller: LLMCaller
    context_window: int
    threshold_pct: float = 0.7
    head_keep: int = 4
    tail_keep: int = 6
    max_passes: int = 3

    @property
    def threshold_tokens(self) -> int:
        """Token threshold above which a preflight should trigger a
        compression. The agent_node preflight uses ``>=`` against this
        value to decide whether to call :meth:`compress`."""
        return int(self.context_window * self.threshold_pct)

    def should_compress(self, messages: Sequence[BaseMessage]) -> bool:
        """Cheap preflight — returns ``True`` if the estimated prompt
        size meets or exceeds the threshold."""
        return estimate_tokens(messages) >= self.threshold_tokens

    async def compress(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        """Compress the message list until it fits under the threshold.

        Returns a new list — never mutates the input. Raises
        :class:`ContextOverflowError` if ``max_passes`` attempts
        cannot bring the estimate below threshold.
        """
        current: list[BaseMessage] = list(messages)
        for pass_idx in range(self.max_passes):
            if estimate_tokens(current) < self.threshold_tokens:
                if pass_idx > 0:
                    logger.info(
                        "context_compressor.compressed passes=%d final_tokens=%d",
                        pass_idx,
                        estimate_tokens(current),
                    )
                return current
            try:
                current = await self._compress_once(current)
            except ContextOverflowError:
                raise
            except Exception as exc:  # pragma: no cover — defensive
                # The summariser is best-effort; if its own call
                # blows up the run cannot continue (we'd be stuck in
                # the same overflow state forever). Re-raise as
                # ContextOverflowError so the orchestrator surfaces a
                # clean failure.
                logger.exception("context_compressor.summariser_failed")
                raise ContextOverflowError(
                    estimated_tokens=estimate_tokens(current),
                    threshold=self.threshold_tokens,
                    passes=pass_idx,
                ) from exc
        if estimate_tokens(current) >= self.threshold_tokens:
            raise ContextOverflowError(
                estimated_tokens=estimate_tokens(current),
                threshold=self.threshold_tokens,
                passes=self.max_passes,
            )
        return current

    async def _compress_once(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """One summarise-the-middle pass."""
        split = _split(messages, head_keep=self.head_keep, tail_keep=self.tail_keep)
        if not split.middle:
            # Nothing summarisable — head+tail already span the
            # remainder. Surfacing this as an overflow is the right
            # signal because the only knobs left to turn are the
            # head/tail-keep counts (manifest-level) or a bigger
            # window.
            raise ContextOverflowError(
                estimated_tokens=estimate_tokens(messages),
                threshold=self.threshold_tokens,
                passes=0,
            )
        transcript = _format_middle_for_summary(split.middle)
        summary_text = await self._summarise(transcript)
        wrapped = SystemMessage(
            content=f"{_SUMMARY_TAG_OPEN}\n{summary_text}\n{_SUMMARY_TAG_CLOSE}"
        )
        return [*split.leading_systems, *split.head, wrapped, *split.tail]

    async def _summarise(self, transcript: str) -> str:
        """Invoke the summariser LLM and return the summary body."""
        prompt = [
            SystemMessage(content=_SUMMARISER_SYSTEM_PROMPT),
            HumanMessage(content=transcript),
        ]
        response = await self.llm_caller(messages=prompt, tools=[])
        return _message_to_text(response).strip() or "(no summary produced)"
