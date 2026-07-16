import json

import httpx
import pytest

from expert_work.runtime.middleware import (
    LLMClientError,
    LLMRateLimitError,
    LLMServerError,
)
from orchestrator.llm.providers.anthropic import HTTPAnthropicClient, RecordingAnthropicClient


def _sse(*events: dict) -> bytes:
    parts = []
    for e in events:
        parts.append(f"event: {e['type']}\ndata: {json.dumps(e)}")
    return ("\n\n".join(parts) + "\n\n").encode()


async def _collect(client: HTTPAnthropicClient) -> list[dict]:
    return [
        dict(e)
        async for e in client.stream_messages(
            model="claude-x",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=1024,
        )
    ]


@pytest.mark.asyncio
async def test_stream_yields_events_until_message_stop() -> None:
    body = _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hi"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    events = await _collect(client)
    assert events[0]["type"] == "message_start"
    assert events[1]["delta"]["text"] == "Hi"
    assert events[-1]["type"] == "message_stop"


@pytest.mark.asyncio
async def test_stream_sets_stream_true_on_wire() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return httpx.Response(200, content=_sse({"type": "message_stop"}))

    transport = httpx.MockTransport(handler)
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    await _collect(client)
    assert captured["stream"] is True


@pytest.mark.asyncio
async def test_stream_http_400_classifies_before_first_event() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(400, text="bad request"))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    with pytest.raises(LLMClientError):
        await _collect(client)


@pytest.mark.asyncio
async def test_stream_in_band_error_event_raises_after_good_events() -> None:
    raw = (
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"partial"}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
        b'"message":"overloaded"}}\n\n'
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    seen: list[str] = []
    with pytest.raises(LLMServerError):
        async for e in client.stream_messages(
            model="m",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=16,
        ):
            if e.get("type") == "content_block_delta":
                seen.append(e["delta"]["text"])
    assert seen == ["partial"]


@pytest.mark.asyncio
async def test_recording_client_streams_canned_events() -> None:
    client = RecordingAnthropicClient(
        stream_events=[
            {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "a"},
            },
            {"type": "message_stop"},
        ]
    )
    out = [
        dict(e)
        async for e in client.stream_messages(
            model="m",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=16,
        )
    ]
    assert [e["type"] for e in out] == ["message_start", "content_block_delta", "message_stop"]
    assert client.calls[-1]["stream"] is True


@pytest.mark.asyncio
async def test_recording_client_synthesizes_events_from_response() -> None:
    # When stream_events is empty, stream_messages() must synthesize a coherent
    # event sequence from `response` (used by T4's response=-primed tests).
    client = RecordingAnthropicClient(
        response={
            "model": "claude-x",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"q": "hi"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
    )
    events = [
        dict(e)
        async for e in client.stream_messages(
            model="claude-x",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=1024,
        )
    ]
    types = [e["type"] for e in events]
    # message_start ... (text delta) (tool_use start + json delta) ... message_delta, message_stop
    assert types[0] == "message_start"
    assert types[-1] == "message_stop"
    assert types[-2] == "message_delta"
    # message_start carries input_tokens; message_delta carries output_tokens
    assert events[0]["message"]["usage"]["input_tokens"] == 5
    assert events[-2]["usage"]["output_tokens"] == 3
    # text block -> a text_delta
    text_deltas = [
        e
        for e in events
        if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta"
    ]
    assert any(d["delta"]["text"] == "hello" for d in text_deltas)
    # tool_use block -> a content_block_start (carries id+name) + an input_json_delta
    starts = [e for e in events if e.get("type") == "content_block_start"]
    assert any(
        s["content_block"]["id"] == "toolu_1" and s["content_block"]["name"] == "search"
        for s in starts
    )
    json_deltas = [
        e
        for e in events
        if e.get("type") == "content_block_delta"
        and e.get("delta", {}).get("type") == "input_json_delta"
    ]
    assert any(json.loads(d["delta"]["partial_json"]) == {"q": "hi"} for d in json_deltas)


@pytest.mark.asyncio
async def test_stream_in_band_rate_limit_error_maps_to_rate_limit() -> None:
    # rate_limit_error is Anthropic's 429 analog -> LLMRateLimitError (key-level),
    # distinct from overloaded_error -> LLMServerError (provider-level).
    raw = (
        b'event: error\ndata: {"type":"error","error":'
        b'{"type":"rate_limit_error","message":"slow down"}}\n\n'
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPAnthropicClient(api_key="k", base_url="https://x", transport=transport)
    with pytest.raises(LLMRateLimitError):
        async for _ in client.stream_messages(
            model="m",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            max_tokens=16,
        ):
            pass
