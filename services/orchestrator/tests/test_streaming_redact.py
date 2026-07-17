import pytest

from expert_work.common.dlp import scan_and_redact
from orchestrator.graph_builder.streaming_redact import HOLD_CHARS, StreamingRedactor, TokenSink, make_token_sink
from orchestrator.llm.providers._streaming import LLMDelta


def test_no_guards_passthrough_progressive() -> None:
    r = StreamingRedactor(dlp=False, screen=False)
    text = "A" * 100
    out1 = r.feed(text)
    assert out1 == "A" * (100 - HOLD_CHARS)  # holds the trailing HOLD_CHARS
    out2 = r.flush()
    assert out1 + out2 == text


def test_short_input_all_at_flush() -> None:
    r = StreamingRedactor(dlp=False, screen=False)
    assert r.feed("hello") == ""  # < HOLD_CHARS → nothing stable to release yet
    assert r.flush() == "hello"


def test_dlp_redacts_card_split_across_feeds() -> None:
    r = StreamingRedactor(dlp=True, screen=False)
    a = r.feed("your card is 4111 1111 ")
    b = r.feed("1111 1111 thanks")
    tail = r.flush()
    full = a + b + tail
    assert "4111" not in full  # raw digits never leaked
    assert full == "your card is [redacted] thanks"


def test_prefix_monotonic_chunked_equals_oneshot() -> None:
    text = "call 4111 1111 1111 1111 or 13800138000 now " + "x" * 80
    r = StreamingRedactor(dlp=True, screen=False)
    out = "".join(r.feed(c) for c in text) + r.flush()
    assert out == scan_and_redact(text).redacted


def test_screen_block_withholds_all() -> None:
    r = StreamingRedactor(dlp=False, screen=True)
    key = "sk-" + "a" * 24  # matches output_screen _SECRET_PATTERNS
    assert r.feed("here is the key " + key) == ""
    assert r.feed(" more text") == ""  # stays blocked
    assert r.flush() == ""


def test_screen_off_does_not_block_credentials() -> None:
    r = StreamingRedactor(dlp=False, screen=False)
    key = "sk-" + "a" * 24
    out = r.feed("key " + key) + r.flush()
    assert key in out  # screen disabled → not withheld


@pytest.mark.asyncio
async def test_token_sink_publishes_content_frames() -> None:
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=3, publish=pub, dlp=False, screen=False)
    await sink(LLMDelta(content="A" * 100))
    await sink.flush()
    assert all(f["step"] == 3 and f["channel"] == "content" for f in frames)
    assert "".join(f["text"] for f in frames) == "A" * 100


@pytest.mark.asyncio
async def test_token_sink_redacts_pii() -> None:
    frames: list[dict] = []

    async def pub(f: dict) -> None:
        frames.append(f)

    sink = TokenSink(step=0, publish=pub, dlp=True, screen=False)
    await sink(LLMDelta(content="card 4111 1111 1111 1111 done " + "x" * 60))
    await sink.flush()
    joined = "".join(f["text"] for f in frames)
    assert "4111" not in joined and "[redacted]" in joined


async def _noop_pub(f: dict) -> None:
    return None


def test_make_token_sink_gates_off_when_judge_enabled() -> None:
    assert make_token_sink(step=0, publish=_noop_pub, dlp=False, screen=False, judge_enabled=True) is None


def test_make_token_sink_none_without_publish() -> None:
    assert make_token_sink(step=0, publish=None, dlp=False, screen=False, judge_enabled=False) is None


def test_make_token_sink_builds_when_enabled() -> None:
    sink = make_token_sink(step=1, publish=_noop_pub, dlp=True, screen=True, judge_enabled=False)
    assert isinstance(sink, TokenSink)
