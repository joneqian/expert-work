"""Dynamic-context middleware — Stream E.3.

Caps the LLM-facing message view at ``max_turns`` and ``max_tokens`` to
keep per-call token cost bounded on long ReAct sessions. Per
[STREAM-E-DESIGN § 2.2 + Mini-ADR E-3](../../../../../../../docs/streams/STREAM-E-DESIGN.md),
M0 deliberately ships the naïve "keep most recent N turns under token
budget" trim — RAG / summarization / cross-session memory ship in M2-C.

Reads and writes ``ctx.payload["messages"]``. The source of that list is
whatever the orchestrator populated upstream (LangGraph state, in M0); the
middleware operates only on the transient LLM-facing view so the
persistent state checkpoint keeps the full history.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import cast

from langchain_core.messages import BaseMessage, SystemMessage

from expert_work.runtime.middleware.base import CallNext, MiddlewareContext


def default_token_estimator(message: BaseMessage) -> int:
    """Anthropic-documented rule of thumb: ~4 characters per token.

    Good enough for budget gating in M0; replace with a tokenizer-backed
    counter (tiktoken / anthropic-tokenizer) in M1 once accuracy starts
    mattering (e.g. when context approaches Claude's 200k limit).
    """
    content = message.content
    if isinstance(content, str):
        length = len(content)
    else:
        length = len(str(content))
    return max(1, length // 4)


@dataclass
class DynamicContextMiddleware:
    """Trim the LLM-facing messages list to fit ``max_turns`` and ``max_tokens``.

    Trimming rules (in order):

    1. ``SystemMessage`` entries are **never** dropped. Removing them
       would change the prompt prefix and invalidate Anthropic's prefix
       cache for every subsequent call.
    2. Among non-system messages, keep the most recent up to
       ``max_turns`` items.
    3. Then walk newest → oldest, dropping the oldest end first until
       cumulative tokens fit under ``max_tokens - system_tokens``.
    4. The **newest** non-system message is always kept even if it
       alone exceeds the remaining budget — better to send an oversized
       prompt and let the LLM tier surface the error than to send an
       empty conversation that the model can't respond to.

    The middleware writes the trimmed list back to ``ctx.payload["messages"]``
    and calls ``call_next`` exactly once. No async I/O; runtime cost is
    O(len(messages)).
    """

    max_turns: int = 20
    max_tokens: int = 8000
    name: str = "dynamic_context"
    anchor: str = "before_llm_call"
    after: tuple[str, ...] = field(default_factory=tuple)
    #: ``pii_redact`` lands in E.5; until then this is a soft / forward
    #: dependency that the chain's topological sort silently skips.
    before: tuple[str, ...] = field(default_factory=lambda: ("pii_redact",))
    token_estimator: Callable[[BaseMessage], int] = field(default=default_token_estimator)

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        raw = ctx.payload.get("messages")
        if not raw:
            await call_next(ctx)
            return

        messages = cast(Sequence[BaseMessage], raw)
        trimmed = self._trim(messages)
        ctx.payload["messages"] = trimmed
        await call_next(ctx)

    def _trim(self, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        regular = [m for m in messages if not isinstance(m, SystemMessage)]

        # Step 2: max_turns cap on the non-system tail.
        if len(regular) > self.max_turns:
            regular = regular[-self.max_turns :]

        # Step 3 + 4: token-budget trim from oldest, always keep newest.
        system_tokens = sum(self.token_estimator(m) for m in system_msgs)
        budget = self.max_tokens - system_tokens
        if budget <= 0 or not regular:
            # System messages alone exceed budget — pass through as-is.
            return list(system_msgs) + regular

        kept_reversed: list[BaseMessage] = []
        running = 0
        for idx, msg in enumerate(reversed(regular)):
            cost = self.token_estimator(msg)
            if idx > 0 and running + cost > budget:
                break
            kept_reversed.append(msg)
            running += cost
        kept = list(reversed(kept_reversed))

        return list(system_msgs) + kept
