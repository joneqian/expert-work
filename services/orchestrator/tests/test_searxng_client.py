"""Unit tests for :class:`SearXNGClient` — the free self-hosted web_search
backend (design ``web-search-searxng-builtin-and-tavily-mcp``)."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from orchestrator.tools import SearXNGClient


def _searxng_body(n: int) -> dict[str, Any]:
    return {
        "query": "q",
        "results": [
            {
                "title": f"title {i}",
                "url": f"https://example.com/{i}",
                "content": f"snippet {i}",
                "engine": "duckduckgo",  # extra key — ignored by the mapping
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_search_maps_to_title_url_content() -> None:
    seen: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_searxng_body(3))

    client = SearXNGClient(base_url="http://searxng:8080", transport=httpx.MockTransport(_handler))
    body = await client.search(query="hello world", max_results=5, tenant_id=uuid4())

    assert body == {
        "results": [
            {"title": "title 0", "url": "https://example.com/0", "content": "snippet 0"},
            {"title": "title 1", "url": "https://example.com/1", "content": "snippet 1"},
            {"title": "title 2", "url": "https://example.com/2", "content": "snippet 2"},
        ]
    }
    # GET /search?q=...&format=json
    assert "/search" in seen["url"]
    assert "q=hello" in seen["url"]
    assert "format=json" in seen["url"]


@pytest.mark.asyncio
async def test_search_slices_to_max_results() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_searxng_body(30))

    client = SearXNGClient(base_url="http://searxng:8080", transport=httpx.MockTransport(_handler))
    body = await client.search(query="q", max_results=4, tenant_id=None)

    # SearXNG has no per-request cap; the client slices to max_results.
    assert len(body["results"]) == 4


@pytest.mark.asyncio
async def test_search_tolerates_missing_results_key() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": "q"})  # no "results"

    client = SearXNGClient(base_url="http://searxng:8080", transport=httpx.MockTransport(_handler))
    assert await client.search(query="q", max_results=5, tenant_id=None) == {"results": []}


@pytest.mark.asyncio
async def test_search_handles_partial_rows() -> None:
    body = {"results": [{"title": "only title"}, "not-a-dict", {"url": "https://x"}]}

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = SearXNGClient(base_url="http://searxng:8080", transport=httpx.MockTransport(_handler))
    out = await client.search(query="q", max_results=5, tenant_id=None)

    # Non-dict rows dropped; missing fields default to "".
    assert out == {
        "results": [
            {"title": "only title", "url": "", "content": ""},
            {"title": "", "url": "https://x", "content": ""},
        ]
    }


@pytest.mark.asyncio
async def test_search_strips_trailing_slash_in_base_url() -> None:
    seen: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_searxng_body(0))

    client = SearXNGClient(base_url="http://searxng:8080/", transport=httpx.MockTransport(_handler))
    await client.search(query="q", max_results=5, tenant_id=None)
    assert seen["path"] == "/search"  # no double slash


@pytest.mark.asyncio
async def test_search_raises_on_http_error() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = SearXNGClient(base_url="http://searxng:8080", transport=httpx.MockTransport(_handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.search(query="q", max_results=5, tenant_id=None)


@pytest.mark.asyncio
async def test_search_raises_on_non_object_body() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=json.dumps([1, 2, 3]), headers={"content-type": "application/json"}
        )

    client = SearXNGClient(base_url="http://searxng:8080", transport=httpx.MockTransport(_handler))
    with pytest.raises(httpx.HTTPError):
        await client.search(query="q", max_results=5, tenant_id=None)
