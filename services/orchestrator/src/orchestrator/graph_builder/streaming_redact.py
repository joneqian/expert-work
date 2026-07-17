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

#: Look-back behind the emission hold that sizes the screen scan window. Must be
#: >= every BLOCK guard's MAX minimum-match length (39, Google API key) so a
#: credential's minimum match is fully inside the last WINDOW chars the feed it
#: completes — caught and latched before its head crosses the emission frontier.
#: (Bounded-equivalence correctness does NOT depend on this value — it is upheld
#: by the exact split-equality check in ``_advance_frozen``, not by a no-straddle
#: margin. This only sizes the screen window and the per-feed rescan cost.)
RESCAN_LOOKBACK = 64

#: Size of the raw-buffer suffix rescanned on each ``feed`` (the screen scan
#: window and the frozen-pointer target lag). Everything before a *verified-clean*
#: frozen boundary is finalized and never rescanned again — this is what makes
#: ``feed`` O(1) amortized (O(n) over the whole stream) instead of O(n) per delta.
WINDOW = HOLD_CHARS + RESCAN_LOOKBACK


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
        #: Redacted chars emitted so far (monotonic; = old ``_emitted_len``).
        self._emitted_out = 0
        #: Raw offset of the finalized boundary: ``_buf[:_frozen_raw]`` redaction
        #: is settled and already emitted, so it is never rescanned again.
        self._frozen_raw = 0
        #: Redacted-char count of ``_buf[:_frozen_raw]``. Invariant (upheld by
        #: the collapse guard in ``_advance_frozen``): ``_frozen_out <= _emitted_out``.
        self._frozen_out = 0
        self._blocked = False

    def _redact(self, text: str) -> str:
        return scan_and_redact(text).redacted if self._dlp else text

    def _window_start(self) -> int:
        return max(0, len(self._buf) - WINDOW)

    def _advance_frozen(self, tail_red: str) -> None:
        # Push the frozen boundary up to ``end - WINDOW`` so the rescanned tail
        # stays bounded — but only if the buffer's redaction splits EXACTLY at
        # new_frozen: redact(head) ++ redact(retained) == the full-context
        # tail_red. This is load-bearing and NOT replaceable by a prefix test:
        # new_frozen (a function of total buffer length) drifts backward into an
        # EARLIER match as the buffer grows, and because ``[redacted]`` is a
        # fixed token, a cut that forms a DIFFERENT collapsing match (e.g. an
        # 18-char id-card's 16-digit prefix independently matches the card shape)
        # produces the same ``[redacted]`` and would fool ``startswith``. Exact
        # split-equality is the real cleanliness invariant; if it fails,
        # new_frozen straddles a match — defer advancing this feed.
        new_frozen = max(0, len(self._buf) - WINDOW)
        if new_frozen <= self._frozen_raw:
            return
        head_red = self._redact(self._buf[self._frozen_raw : new_frozen])
        retained_red = self._redact(self._buf[new_frozen:])
        if head_red + retained_red != tail_red:
            return
        added = len(head_red)
        # Collapse guard: a PII span completing just past the emission frontier
        # makes ``redact`` shrink, so a clean-split frozen count can momentarily
        # exceed what we've emitted; freezing it would drive
        # ``_frozen_out > _emitted_out`` and a later negative slice index
        # (Python wraps → tail leak). Defer until emission catches up.
        if self._frozen_out + added > self._emitted_out:
            return
        self._frozen_out += added
        self._frozen_raw = new_frozen

    def feed(self, text: str) -> str:
        if self._blocked:
            return ""
        self._buf += text
        if not text:
            return ""
        if self._screen and screen_output(self._buf[self._window_start() :]).blocked:
            self._blocked = True
            return ""
        tail_red = self._redact(self._buf[self._frozen_raw :])
        full_red_len = self._frozen_out + len(tail_red)
        boundary = max(self._emitted_out, full_red_len - HOLD_CHARS)
        out = tail_red[self._emitted_out - self._frozen_out : boundary - self._frozen_out]
        self._emitted_out = boundary
        self._advance_frozen(tail_red)
        return out

    def flush(self) -> str:
        if self._blocked:
            return ""
        if self._screen and screen_output(self._buf[self._window_start() :]).blocked:
            self._blocked = True
            return ""
        tail_red = self._redact(self._buf[self._frozen_raw :])
        out = tail_red[self._emitted_out - self._frozen_out :]
        self._emitted_out = self._frozen_out + len(tail_red)
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
