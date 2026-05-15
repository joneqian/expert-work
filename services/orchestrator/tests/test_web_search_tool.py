"""Unit tests for :class:`WebSearchTool` (Stream E.7)."""

from __future__ import annotations

import pytest

from orchestrator import Tool
from orchestrator.tools import (
    DEFAULT_CONTENT_CHAR_CAP,
    DEFAULT_MAX_RESULTS,
    RecordingTavilyClient,
    TavilyClient,
    WebSearchTool,
)


def _result(title: str = "T", url: str = "https://example.com", content: str = "body") -> dict:
    return {"title": title, "url": url, "content": content}


def _client(results: list[dict] | None = None) -> RecordingTavilyClient:
    return RecordingTavilyClient(results=tuple(results or []))


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_search_returns_formatted_results() -> None:
    client = _client(
        [
            _result(
                title="Helix on GitHub",
                url="https://github.com/x",
                content="An agent runtime",
            ),
            _result(title="Docs", url="https://docs.x", content="Quickstart"),
        ]
    )
    tool = WebSearchTool(client=client)
    result = await tool.call({"query": "helix-agent"})

    assert result.meta == {"truncated": False, "n_results": 2}
    assert "Helix on GitHub" in result.content
    assert "https://github.com/x" in result.content
    assert "An agent runtime" in result.content
    assert client.last_query == "helix-agent"
    assert client.last_max_results == DEFAULT_MAX_RESULTS


@pytest.mark.asyncio
async def test_max_results_caps_request_and_response() -> None:
    client = _client([_result(content=f"r{i}") for i in range(10)])
    tool = WebSearchTool(client=client)
    result = await tool.call({"query": "x", "max_results": 3})

    assert client.last_max_results == 3
    # Even if the API returns more, the tool truncates to max_results.
    assert result.meta["n_results"] == 3


@pytest.mark.asyncio
async def test_max_results_above_cap_is_clamped_to_ten() -> None:
    client = _client()
    tool = WebSearchTool(client=client)
    await tool.call({"query": "x", "max_results": 999})
    assert client.last_max_results == 10


@pytest.mark.asyncio
async def test_max_results_below_one_is_clamped_to_one() -> None:
    client = _client()
    tool = WebSearchTool(client=client)
    await tool.call({"query": "x", "max_results": 0})
    assert client.last_max_results == 1


@pytest.mark.asyncio
async def test_max_results_bool_is_rejected_and_uses_default() -> None:
    """``bool`` is a subclass of ``int`` — guard against LLM-supplied ``True``."""
    client = _client()
    tool = WebSearchTool(client=client)
    await tool.call({"query": "x", "max_results": True})
    assert client.last_max_results == DEFAULT_MAX_RESULTS


# ---------------------------------------------------------------------------
# Truncation (Mini-ADR E-10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_content_truncated_with_marker() -> None:
    long = "a" * (DEFAULT_CONTENT_CHAR_CAP + 5000)
    client = _client([_result(content=long)])
    tool = WebSearchTool(client=client)
    result = await tool.call({"query": "x"})

    assert result.meta["truncated"] is True
    assert "...[truncated]" in result.content
    # Body must contain the cap-prefix plus marker (well under raw length).
    assert len(result.content) < DEFAULT_CONTENT_CHAR_CAP + 1000


@pytest.mark.asyncio
async def test_short_content_not_marked_truncated() -> None:
    client = _client([_result(content="short body")])
    tool = WebSearchTool(client=client)
    result = await tool.call({"query": "x"})

    assert result.meta["truncated"] is False
    assert "[truncated]" not in result.content


@pytest.mark.asyncio
async def test_mixed_truncation_marks_meta_true() -> None:
    """One long + one short → meta.truncated remains True."""
    long = "a" * (DEFAULT_CONTENT_CHAR_CAP + 100)
    client = _client(
        [
            _result(content="short"),
            _result(content=long),
        ]
    )
    tool = WebSearchTool(client=client)
    result = await tool.call({"query": "x"})
    assert result.meta["truncated"] is True
    assert result.meta["n_results"] == 2


# ---------------------------------------------------------------------------
# Empty / malformed payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_results_returns_no_results_marker() -> None:
    tool = WebSearchTool(client=_client([]))
    result = await tool.call({"query": "no hits"})
    assert result.content == "(no results)"
    assert result.meta == {"truncated": False, "n_results": 0}


@pytest.mark.asyncio
async def test_payload_without_results_key_handled_safely() -> None:
    class _BareClient:
        async def search(self, *, query: str, max_results: int) -> dict:
            return {}

    tool = WebSearchTool(client=_BareClient())
    result = await tool.call({"query": "x"})
    assert result.content == "(no results)"
    assert result.meta == {"truncated": False, "n_results": 0}


@pytest.mark.asyncio
async def test_non_mapping_entries_skipped() -> None:
    """Tavily occasionally returns malformed rows; tolerate them."""

    class _MalformedClient:
        async def search(self, *, query: str, max_results: int) -> dict:
            return {
                "results": [
                    _result(content="ok"),
                    "not-a-dict",  # type: ignore[list-item]
                    {"content": "still ok"},
                ]
            }

    tool = WebSearchTool(client=_MalformedClient())
    result = await tool.call({"query": "x"})
    # 2 valid entries; the string is silently skipped.
    assert result.meta["n_results"] == 2


# ---------------------------------------------------------------------------
# Input validation + error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_query_raises_value_error() -> None:
    tool = WebSearchTool(client=_client())
    with pytest.raises(ValueError, match="query"):
        await tool.call({})


@pytest.mark.asyncio
async def test_blank_query_raises_value_error() -> None:
    tool = WebSearchTool(client=_client())
    with pytest.raises(ValueError, match="query"):
        await tool.call({"query": "   "})


@pytest.mark.asyncio
async def test_client_exception_propagates_to_caller() -> None:
    """E.6 graph wraps tool exceptions into ToolMessage(error). The tool
    itself must NOT swallow client errors — let them propagate so the
    ReAct tools_node can centrally apply the error-message convention."""
    client = RecordingTavilyClient(raise_on_search=RuntimeError("api down"))
    tool = WebSearchTool(client=client)
    with pytest.raises(RuntimeError, match="api down"):
        await tool.call({"query": "x"})


# ---------------------------------------------------------------------------
# Spec + protocol contract
# ---------------------------------------------------------------------------


def test_spec_declares_query_and_max_results_schema() -> None:
    spec = WebSearchTool(client=_client()).spec
    assert spec.name == "web_search"
    params = spec.parameters
    assert "query" in params["properties"]
    assert "max_results" in params["properties"]
    assert params["required"] == ["query"]


def test_satisfies_tool_protocol() -> None:
    assert isinstance(WebSearchTool(client=_client()), Tool)


def test_recording_client_satisfies_tavily_client_protocol() -> None:
    assert isinstance(RecordingTavilyClient(), TavilyClient)
