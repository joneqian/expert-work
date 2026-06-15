"""Context-pressure feedback middleware — 3.3 (★3→★5).

The context cascade (CM-2 working window → L.L2 compressor → E.3 view trim)
keeps the prompt under ``context_window * threshold_pct`` by **silently**
dropping or summarising history. The model never learns it is near its
budget, so it can't *behave* differently — keep starting new lines of work,
spawn sub-tasks, expand — right up until something is forcibly cut.

This middleware closes that blind spot: it measures the about-to-be-sent
prompt against the model's resolved ``context_window`` and, when usage
crosses ``warn_pct``, appends a short **model-visible** budget note to the
last message so the agent can converge (summarise, conclude) on its own.

Design choices (see docs/research/2026-06-15-33-context-pressure-feedback-design.md):

* **Anchor ``before_llm_call``, after ``dynamic_context``** — measure the
  real post-trim view that will actually be sent.
* **Append to the last message, never the leading system prompt** — keeps
  Anthropic's prefix cache intact (the cache matches the longest common
  prefix; only the tail changes).
* **Denominator = the model's context window**, not the trim cap — usage vs
  the cap is ~always full post-trim and says nothing; usage vs the real
  window reflects true pressure (the warning fires only when the cascade
  could not relieve it, i.e. when convergence is genuinely warranted).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage

from helix_agent.runtime.middleware.base import CallNext, MiddlewareContext
from helix_agent.runtime.middleware.dynamic_context import default_token_estimator


@dataclass
class ContextPressureMiddleware:
    """Append a budget note to the last message when prompt usage is high.

    ``context_window`` is the model's resolved window (the control-plane
    passes ``_resolved_context_window``); a non-positive value disables the
    middleware (it can't compute a ratio). ``warn_pct`` is the usage
    fraction at/above which the note is injected.
    """

    context_window: int
    warn_pct: float = 0.75
    name: str = "context_pressure"
    anchor: str = "before_llm_call"
    #: Run after the E.3 trim so the measured view is what actually ships.
    after: tuple[str, ...] = field(default_factory=lambda: ("dynamic_context",))
    before: tuple[str, ...] = field(default_factory=tuple)
    token_estimator: Callable[[BaseMessage], int] = field(default=default_token_estimator)

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        messages = ctx.payload.get("messages")
        if messages:
            note = self._pressure_note(messages)
            if note is not None:
                ctx.payload["messages"] = self._with_note(messages, note)
        await call_next(ctx)

    def _pressure_note(self, messages: Sequence[BaseMessage]) -> str | None:
        if self.context_window <= 0:
            return None
        prompt_tokens = sum(self.token_estimator(m) for m in messages)
        used = prompt_tokens / self.context_window
        if used < self.warn_pct:
            return None
        remaining = max(0, self.context_window - prompt_tokens)
        pct = min(100, round(used * 100))
        return (
            f"[Context budget: ~{remaining} of {self.context_window} tokens remaining "
            f"({pct}% used). You are nearing the context limit — prioritise summarising "
            f"progress and concluding over starting new lines of work.]"
        )

    @staticmethod
    def _with_note(messages: Sequence[BaseMessage], note: str) -> list[BaseMessage]:
        """Return a new list with the note appended to the last message.

        Immutable: the last message is copied (``model_copy``) rather than
        mutated, and every earlier message is passed through unchanged so
        the prompt prefix — and the provider's prefix cache — is preserved.
        """
        out = list(messages)
        last = out[-1]
        content = last.content
        if isinstance(content, str):
            new_content: str | list[object] = f"{content}\n\n{note}" if content else note
        else:
            # Content-block list (multimodal): append a trailing text part.
            new_content = [*content, {"type": "text", "text": note}]
        out[-1] = last.model_copy(update={"content": new_content})
        return out
