"""Tavily ``web_search`` tool — Stream E.7.

First concrete :class:`Tool` implementation. Wraps Tavily's REST API
(``POST /search``) behind a small :class:`TavilyClient` Protocol so
production deployments use :class:`HTTPTavilyClient` (httpx) while
tests inject :class:`RecordingTavilyClient` with scripted results.

Output truncation per § 1.1 E.7 + Mini-ADR E-10 in
[STREAM-E-DESIGN](../../../../../docs/streams/STREAM-E-DESIGN.md)
(updated in PR #62):

- Default ``max_results=5`` — capped before any content rendering.
- Each result's ``content`` capped at 4096 characters (head-truncation
  with ``...[truncated]`` marker, matching deer-flow
  ``community/tavily/tools.py`` semantics).
- If any result was truncated, ``ToolResult.meta["truncated"] = True``
  so the LLM can choose to re-query with a narrower term.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import httpx

from orchestrator.tools.registry import ToolContext, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

#: Per [STREAM-E-DESIGN § 1.1 E.7], deer-flow-aligned defaults.
DEFAULT_MAX_RESULTS = 5
DEFAULT_CONTENT_CHAR_CAP = 4096
_TRUNCATION_MARKER = "...[truncated]"
_DEFAULT_BASE_URL = "https://api.tavily.com"
_DEFAULT_TIMEOUT_S = 15.0
#: SearXNG is internal infra (same datacenter / docker network) so a tight
#: timeout is fine; it still has to fan out to upstream engines, hence 15s.
_DEFAULT_SEARXNG_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# Tavily client protocol + implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class TavilyClient(Protocol):
    """Tavily REST surface this tool depends on. Sized to the one
    endpoint we use so tests can fake it without mocking httpx."""

    async def search(
        self, *, query: str, max_results: int, tenant_id: UUID | None
    ) -> Mapping[str, Any]:
        """POST ``/search`` and return the parsed JSON body.

        Expected shape: ``{"results": [{"title": str, "url": str, "content": str}, ...]}``.
        Other keys (``answer``, ``response_time``) are tolerated and
        ignored by :class:`WebSearchTool`.

        Stream O (Mini-ADR O-9) — ``tenant_id`` lets a credential-resolving
        client pick the per-tenant Tavily key. Fixed-key / test clients
        accept and ignore it.
        """


@dataclass
class HTTPTavilyClient:
    """Production :class:`TavilyClient` — calls Tavily's REST API.

    Uses httpx directly rather than the ``tavily-python`` SDK to keep
    deps minimal. Raises :class:`httpx.HTTPError` subclasses on
    network / server failures; :class:`WebSearchTool.call` lets them
    propagate so the ReAct ``tools`` node (E.6) wraps them into a
    ``ToolMessage(status="error")`` per Mini-ADR E-12.
    """

    api_key: str
    base_url: str = _DEFAULT_BASE_URL
    timeout_s: float = _DEFAULT_TIMEOUT_S

    async def search(
        self, *, query: str, max_results: int, tenant_id: UUID | None = None
    ) -> Mapping[str, Any]:
        del tenant_id  # fixed-key client — credential baked into ``api_key``
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(
                f"{self.base_url}/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                    "include_images": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, Mapping):
                msg = f"tavily returned non-object body: {type(data).__name__}"
                raise httpx.HTTPError(msg)
            return data


@dataclass
class RecordingTavilyClient:
    """In-memory :class:`TavilyClient` for dev / tests.

    Returns the pre-set ``results`` payload regardless of query. Use
    ``raise_on_search`` to assert error-path behaviour without spinning
    up httpx mocks.
    """

    results: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    raise_on_search: Exception | None = None
    last_query: str | None = None
    last_max_results: int | None = None

    async def search(
        self, *, query: str, max_results: int, tenant_id: UUID | None = None
    ) -> Mapping[str, Any]:
        del tenant_id  # test double — no per-tenant key
        self.last_query = query
        self.last_max_results = max_results
        if self.raise_on_search is not None:
            raise self.raise_on_search
        return {"results": list(self.results)}


@dataclass(frozen=True)
class SearXNGClient:
    """:class:`TavilyClient` over a self-hosted SearXNG JSON API.

    SearXNG is a free, open-source (AGPL-3.0) metasearch engine; its
    ``GET /search?format=json`` returns
    ``{"results": [{"title", "url", "content", "engine", ...}]}`` which
    maps near 1:1 onto the shape :class:`WebSearchTool` expects.

    No API key — the instance is platform infrastructure reached over the
    internal network and is NEVER exposed publicly (its JSON API is a free
    upstream proxy; see design ``web-search-searxng-builtin-and-tavily-mcp``
    §3.4). ``tenant_id`` is accepted for protocol conformance and ignored.
    """

    base_url: str
    timeout_s: float = _DEFAULT_SEARXNG_TIMEOUT_S
    transport: httpx.AsyncBaseTransport | None = None

    async def search(
        self, *, query: str, max_results: int, tenant_id: UUID | None = None
    ) -> Mapping[str, Any]:
        del tenant_id  # shared instance — no per-tenant credential
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s) as client:
            response = await client.get(
                f"{self.base_url.rstrip('/')}/search",
                params={"q": query, "format": "json"},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, Mapping):
            msg = f"searxng returned non-object body: {type(data).__name__}"
            raise httpx.HTTPError(msg)
        raw_results = data.get("results")
        results = raw_results if isinstance(raw_results, list) else []
        # SearXNG has no per-request result cap (it returns a full page);
        # slice to the caller's ``max_results`` and normalise to the
        # {title,url,content} shape ``WebSearchTool`` formats.
        valid = [row for row in results if isinstance(row, Mapping)]
        normalised = [
            {
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "content": row.get("content", ""),
            }
            for row in valid[:max_results]
        ]
        return {"results": normalised}


# ---------------------------------------------------------------------------
# The tool itself
# ---------------------------------------------------------------------------


@dataclass
class WebSearchTool:
    """Tavily-backed web search tool exposed to the LLM as ``web_search``.

    The LLM calls with ``{"query": "...", "max_results"?: int}``; the
    tool returns a formatted text block with one entry per result and
    ``meta.truncated`` set when any single result's content exceeded
    the cap.
    """

    client: TavilyClient
    default_max_results: int = DEFAULT_MAX_RESULTS
    content_char_cap: int = DEFAULT_CONTENT_CHAR_CAP

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description=(
                "Search the public web via Tavily. Use for current events, "
                "facts you're unsure about, or any external lookup."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query in natural language.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": (
                            f"Number of results to return (default {DEFAULT_MAX_RESULTS}, cap 10)."
                        ),
                    },
                },
                "required": ["query"],
            },
            # Stream L.L6 — pure read; safe to dispatch concurrently with
            # other read-only tools.
            is_read_only=True,
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        # Stream O (Mini-ADR O-9) — ``ctx.tenant_id`` selects the per-tenant
        # Tavily credential for a credential-resolving client; fixed-key /
        # test clients ignore it.
        query = self._require_query(args)
        max_results = self._coerce_max_results(args.get("max_results"))
        raw = await self.client.search(
            query=query, max_results=max_results, tenant_id=ctx.tenant_id
        )
        return self._format(raw, max_results=max_results)

    # ------------------------------------------------------------------

    def _require_query(self, args: Mapping[str, Any]) -> str:
        raw = args.get("query")
        if not isinstance(raw, str) or not raw.strip():
            msg = "web_search requires a non-empty 'query' string"
            raise ValueError(msg)
        return raw.strip()

    def _coerce_max_results(self, raw: object) -> int:
        if raw is None:
            return self.default_max_results
        if isinstance(raw, bool):
            # ``bool`` is a subclass of ``int``; reject it explicitly so
            # the LLM passing ``True`` doesn't silently become ``1``.
            return self.default_max_results
        if isinstance(raw, int):
            return max(1, min(10, raw))
        return self.default_max_results

    def _format(
        self,
        raw: Mapping[str, Any],
        *,
        max_results: int,
    ) -> ToolResult:
        raw_results = raw.get("results")
        if not isinstance(raw_results, Sequence):
            return ToolResult(
                content="(no results)",
                meta={"truncated": False, "n_results": 0},
            )

        truncated_any = False
        rendered: list[str] = []
        for entry in list(raw_results)[:max_results]:
            if not isinstance(entry, Mapping):
                continue
            content_raw = entry.get("content", "")
            content = content_raw if isinstance(content_raw, str) else str(content_raw)
            if len(content) > self.content_char_cap:
                content = content[: self.content_char_cap] + _TRUNCATION_MARKER
                truncated_any = True
            title = str(entry.get("title", "")).strip()
            url = str(entry.get("url", "")).strip()
            rendered.append(_format_result(title=title, url=url, content=content))

        if not rendered:
            return ToolResult(
                content="(no results)",
                meta={"truncated": False, "n_results": 0},
            )
        return ToolResult(
            content="\n\n".join(rendered),
            meta={"truncated": truncated_any, "n_results": len(rendered)},
        )


def _format_result(*, title: str, url: str, content: str) -> str:
    header = f"# {title}" if title else "# (untitled)"
    pieces = [header]
    if url:
        pieces.append(f"<{url}>")
    pieces.append(content if content else "(empty content)")
    return "\n".join(pieces)
