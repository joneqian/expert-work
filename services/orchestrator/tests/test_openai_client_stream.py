import json

import httpx
import pytest

from expert_work.runtime.middleware import LLMClientError, LLMServerError
from orchestrator.llm.providers.openai import HTTPOpenAIClient, RecordingOpenAIClient


def _sse(*objs: dict) -> bytes:
    lines = [f"data: {json.dumps(o)}" for o in objs] + ["data: [DONE]"]
    return ("\n\n".join(lines) + "\n\n").encode()


async def _collect(client: HTTPOpenAIClient) -> list[dict]:
    return [
        dict(c)
        async for c in client.stream_chat_completions(
            model="glm-5.2", messages=[{"role": "user", "content": "hi"}], tools=None
        )
    ]


@pytest.mark.asyncio
async def test_stream_yields_chunks_and_stops_at_done() -> None:
    body = _sse(
        {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {
            "choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    chunks = await _collect(client)
    assert [c["choices"][0]["delta"].get("content") for c in chunks[:2]] == ["Hel", "lo"]
    assert chunks[-1]["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_stream_skips_keepalive_comments() -> None:
    raw = b': keep-alive\n\ndata: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    chunks = await _collect(client)
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["delta"]["content"] == "x"


@pytest.mark.asyncio
async def test_stream_http_400_classifies_before_first_chunk() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(400, text="bad request"))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    with pytest.raises(LLMClientError):
        await _collect(client)


@pytest.mark.asyncio
async def test_stream_in_band_error_event_raises() -> None:
    raw = (
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        b'data: {"error":{"message":"upstream exploded","type":"server_error"}}\n\n'
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=raw))
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    seen: list[str] = []
    with pytest.raises(LLMServerError):
        async for c in client.stream_chat_completions(
            model="m", messages=[{"role": "user", "content": "hi"}], tools=None
        ):
            seen.append(c["choices"][0]["delta"].get("content", ""))
    assert seen == ["partial"]  # the good chunk was delivered before the error


@pytest.mark.asyncio
async def test_stream_sets_stream_true_and_include_usage() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "x"}}]}))

    transport = httpx.MockTransport(handler)
    client = HTTPOpenAIClient(api_key="k", base_url="https://x", transport=transport)
    await _collect(client)
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_recording_client_streams_canned_chunks() -> None:
    client = RecordingOpenAIClient(
        stream_chunks=[
            {"choices": [{"delta": {"content": "a"}}]},
            {"choices": [{"delta": {"content": "b"}}]},
        ]
    )
    out = [
        dict(c)
        async for c in client.stream_chat_completions(
            model="m", messages=[{"role": "user", "content": "hi"}], tools=None
        )
    ]
    assert [c["choices"][0]["delta"]["content"] for c in out] == ["a", "b"]
    assert client.calls[-1]["stream"] is True
