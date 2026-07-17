"""Buffered-release streaming redaction for token SSE frames (流式 epic 子项目 2).

Token deltas escape the LLM router BEFORE the node-level output guards run on the
assembled message, so a per-delta redaction pass runs here as defense in depth.
Reuses the SAME regex guards as the non-streaming path (``scan_and_redact`` for
DLP, ``screen_output`` for the credential/exfil screen), applied incrementally
with a look-back hold so a pattern split across deltas is never partially emitted.

Token frames are provisional: the authoritative ``updates`` frame (full guards on
the complete message) is the source of truth. This redactor is best-effort on the
preview — the fixed-shape DLP patterns (card/id/phone) fit fully inside the hold
window; anything longer is covered by the authoritative frame. The residual case
is email: the pattern is unbounded, so an address longer than HOLD_CHARS can still
leak a partial head in the provisional preview — the authoritative frame is the
backstop for that case.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from expert_work.common.dlp import scan_and_redact
from expert_work.common.output_screen import screen_output

if TYPE_CHECKING:
    from orchestrator.llm.providers._streaming import LLMDelta

#: Characters held back from the tail of the (redacted) buffer on each feed.
#: Two-sided invariant — HOLD_CHARS must be:
#:   1. >= every BLOCK guard's (``screen_output``) MINIMUM regex-match length,
#:      else a partial credential head could cross the emission boundary and
#:      escape before the screen latch trips.
#:   2. >= every fixed-shape DLP (``scan_and_redact``) pattern's MAXIMUM
#:      match length, so a pattern still completing is always still inside
#:      the hold and never partially redacted.
#: Holds today: screen minimums top out at 39 (Google API key `AIza…{35}`;
#: sk-…{20,}=23, AKIA…=20, xox…=15, PEM=27); DLP fixed-shape maxes top out at
#: 19 (card 19 / id 18 / phone 11 — email is unbounded, see module docstring).
#: A future guard pattern with a larger minimum-match-length would silently
#: defeat the hold.
HOLD_CHARS = 64


class StreamingRedactor:
    """Incremental buffered-release redactor over one content channel.

    ``feed(text)`` returns the newly-stable redacted prefix safe to emit now;
    ``flush()`` returns the redacted remainder at stream end. Only the guards
    enabled for the run (``dlp`` / ``screen``) are applied. ``screen`` is a BLOCK
    guard: once it trips, the redactor withholds everything (the authoritative
    frame carries the refusal).
    """

    def __init__(self, *, dlp: bool, screen: bool) -> None:
        self._dlp = dlp
        self._screen = screen
        self._buf = ""
        self._emitted_len = 0
        self._blocked = False

    def _redact(self, text: str) -> str:
        return scan_and_redact(text).redacted if self._dlp else text

    def feed(self, text: str) -> str:
        if self._blocked:
            return ""
        self._buf += text
        if not text:
            return ""
        if self._screen and screen_output(self._buf).blocked:
            self._blocked = True
            return ""
        redacted = self._redact(self._buf)
        boundary = max(self._emitted_len, len(redacted) - HOLD_CHARS)
        out = redacted[self._emitted_len : boundary]
        self._emitted_len = boundary
        return out

    def flush(self) -> str:
        if self._blocked:
            return ""
        if self._screen and screen_output(self._buf).blocked:
            self._blocked = True
            return ""
        redacted = self._redact(self._buf)
        out = redacted[self._emitted_len :]
        self._emitted_len = len(redacted)
        return out


#: Async callback that ships one token frame to the SSE bridge (injected by
#: run_agent via ``TOKEN_SINK_KEY``; see graph_builder/_config.py).
TokenPublish = Callable[[dict[str, Any]], Awaitable[None]]


class TokenSink:
    """Per-run content-channel token emitter.

    Wraps a :class:`StreamingRedactor`; each streamed ``LLMDelta``'s content is
    redacted incrementally and the newly-stable text is published as a
    ``{"step", "channel": "content", "text"}`` frame. ``flush`` emits the
    buffered-release tail after the router returns.
    """

    def __init__(self, *, step: int, publish: TokenPublish, dlp: bool, screen: bool) -> None:
        self._step = step
        self._publish = publish
        self._redactor = StreamingRedactor(dlp=dlp, screen=screen)

    async def __call__(self, delta: LLMDelta) -> None:
        safe = self._redactor.feed(delta.content)
        if safe:
            await self._publish({"step": self._step, "channel": "content", "text": safe})

    async def flush(self) -> None:
        tail = self._redactor.flush()
        if tail:
            await self._publish({"step": self._step, "channel": "content", "text": tail})


def make_token_sink(
    *,
    step: int,
    publish: TokenPublish | None,
    dlp: bool,
    screen: bool,
    judge_enabled: bool,
) -> TokenSink | None:
    """Build a :class:`TokenSink`, or ``None`` when token streaming is gated off.

    Gate: an LLM output judge (``judge_enabled``) can only decide on the complete
    message, so its runs never token-stream; and without an injected ``publish``
    sink there is nowhere to send frames.
    """
    if judge_enabled or publish is None:
        return None
    return TokenSink(step=step, publish=publish, dlp=dlp, screen=screen)
