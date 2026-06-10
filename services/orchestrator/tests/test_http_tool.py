"""Unit tests for :class:`HTTPTool` (Stream E.8)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import httpx
import pytest

from orchestrator import Tool, ToolContext
from orchestrator.tools import (
    DEFAULT_BODY_CHAR_CAP,
    DEFAULT_HEADER_CHAR_CAP,
    HTTPTool,
    ToolBlockedError,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _allowlist(*patterns: str):
    async def provider(_tenant_id: UUID | None) -> Sequence[str]:
        return patterns

    return provider


def _client_factory(handler):
    """Build a factory that yields an httpx client backed by ``handler``."""

    def factory() -> httpx.AsyncClient:
        transport = httpx.MockTransport(handler)
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    return factory


def _tenant_ctx(tenant_id: UUID | None = None) -> ToolContext:
    return ToolContext(tenant_id=tenant_id or uuid4())


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_allowlist_is_deny_all() -> None:
    """Default ``[]`` ↔ deny-all so freshly-provisioned tenants are safe."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="should not happen")

    tool = HTTPTool(allowlist_provider=_allowlist(), client_factory=_client_factory(handler))
    with pytest.raises(ToolBlockedError, match="empty"):
        await tool.call(
            {"method": "GET", "url": "https://api.github.com/users/x"},
            ctx=_tenant_ctx(),
        )


@pytest.mark.asyncio
async def test_url_not_in_allowlist_blocked() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x")

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.github.com/*"),
        client_factory=_client_factory(handler),
    )
    with pytest.raises(ToolBlockedError, match="not in http_tool_allowlist"):
        await tool.call(
            {"method": "GET", "url": "https://evil.example.com/secrets"},
            ctx=_tenant_ctx(),
        )


@pytest.mark.asyncio
async def test_url_matching_allowlist_passes() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, text='{"login": "octocat"}')

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.github.com/*"),
        client_factory=_client_factory(handler),
    )
    result = await tool.call(
        {"method": "GET", "url": "https://api.github.com/users/octocat"},
        ctx=_tenant_ctx(),
    )
    assert result.meta["status_code"] == 200
    assert "octocat" in result.content
    assert captured[0].method == "GET"
    assert str(captured[0].url) == "https://api.github.com/users/octocat"


@pytest.mark.asyncio
async def test_missing_tenant_id_is_blocked() -> None:
    """Tenant-bound context required — anonymous calls cannot reach the
    network (M0 invariant before F.5 Credential Proxy)."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x")

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://*"),
        client_factory=_client_factory(handler),
    )
    with pytest.raises(ToolBlockedError, match="tenant"):
        await tool.call(
            {"method": "GET", "url": "https://api.example.com/"},
            ctx=ToolContext(tenant_id=None),
        )


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_method_defaults_to_get_and_supports_post_body() -> None:
    seen: list[tuple[str, bytes]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, bytes(req.content)))
        return httpx.Response(204)

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.example.com/*"),
        client_factory=_client_factory(handler),
    )
    # Default GET — no body
    await tool.call({"url": "https://api.example.com/get"}, ctx=_tenant_ctx())
    # POST JSON body
    await tool.call(
        {
            "method": "POST",
            "url": "https://api.example.com/echo",
            "headers": {"X-Test": "yes"},
            "body": {"hello": "world"},
        },
        ctx=_tenant_ctx(),
    )
    assert seen[0][0] == "GET"
    assert seen[0][1] == b""
    assert seen[1][0] == "POST"
    assert b'"hello"' in seen[1][1] and b'"world"' in seen[1][1]


@pytest.mark.asyncio
async def test_string_body_sent_verbatim() -> None:
    seen: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(bytes(req.content))
        return httpx.Response(200)

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.example.com/*"),
        client_factory=_client_factory(handler),
    )
    await tool.call(
        {"method": "POST", "url": "https://api.example.com/", "body": "raw=text"},
        ctx=_tenant_ctx(),
    )
    assert seen[0] == b"raw=text"


@pytest.mark.asyncio
async def test_unsupported_method_raises_value_error() -> None:
    tool = HTTPTool(
        allowlist_provider=_allowlist("https://*"),
        client_factory=_client_factory(lambda _req: httpx.Response(200)),
    )
    with pytest.raises(ValueError, match="unsupported HTTP method"):
        await tool.call(
            {"method": "TRACE", "url": "https://api.example.com/"},
            ctx=_tenant_ctx(),
        )


@pytest.mark.asyncio
async def test_missing_url_raises_value_error() -> None:
    tool = HTTPTool(
        allowlist_provider=_allowlist("https://*"),
        client_factory=_client_factory(lambda _req: httpx.Response(200)),
    )
    with pytest.raises(ValueError, match="url"):
        await tool.call({"method": "GET"}, ctx=_tenant_ctx())


# ---------------------------------------------------------------------------
# Response truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_response_body_tail_truncated() -> None:
    huge = "x" * (DEFAULT_BODY_CHAR_CAP + 5_000)

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=huge)

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.example.com/*"),
        client_factory=_client_factory(handler),
    )
    result = await tool.call(
        {"method": "GET", "url": "https://api.example.com/big"},
        ctx=_tenant_ctx(),
    )
    assert result.meta["truncated"] is True
    assert result.meta["status_code"] == 200
    assert "...[truncated]" in result.content
    # Header section + body cap + framing; well under raw + 1k.
    assert len(result.content) < DEFAULT_BODY_CHAR_CAP + 2_000
    # Stream CM-5: the complete rendering rides along for externalization.
    assert result.full_content is not None
    assert huge in result.full_content
    assert "...[truncated]" not in result.full_content


@pytest.mark.asyncio
async def test_short_response_not_marked_truncated() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hi")

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.example.com/*"),
        client_factory=_client_factory(handler),
    )
    result = await tool.call(
        {"method": "GET", "url": "https://api.example.com/"},
        ctx=_tenant_ctx(),
    )
    assert result.meta["truncated"] is False
    assert result.meta["headers_truncated"] is False
    # Un-truncated response carries no overflow payload (Stream CM-5).
    assert result.full_content is None


@pytest.mark.asyncio
async def test_many_headers_truncated_at_cap() -> None:
    long_value = "v" * 200

    def handler(_req: httpx.Request) -> httpx.Response:
        # Synthesize way over the header cap so truncation kicks in.
        headers = {f"X-Bulk-{i}": long_value for i in range(100)}
        return httpx.Response(200, text="ok", headers=headers)

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.example.com/*"),
        client_factory=_client_factory(handler),
    )
    result = await tool.call(
        {"method": "GET", "url": "https://api.example.com/"},
        ctx=_tenant_ctx(),
    )
    assert result.meta["headers_truncated"] is True
    # The rendered headers section appears before '--- body ---'.
    rendered = result.content.split("--- body ---")[0]
    assert "[truncated]" in rendered
    assert len(rendered) < DEFAULT_HEADER_CHAR_CAP + 200


# ---------------------------------------------------------------------------
# Error propagation (httpx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_error_propagates_to_caller() -> None:
    """Per Mini-ADR E-12, the tool lets transport errors propagate;
    the ReAct ``tools`` node turns them into ``ToolMessage(error)``."""

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    tool = HTTPTool(
        allowlist_provider=_allowlist("https://api.example.com/*"),
        client_factory=_client_factory(handler),
    )
    with pytest.raises(httpx.ConnectError):
        await tool.call(
            {"method": "GET", "url": "https://api.example.com/"},
            ctx=_tenant_ctx(),
        )


# ---------------------------------------------------------------------------
# Spec + protocol contract
# ---------------------------------------------------------------------------


def test_spec_lists_method_url_headers_body() -> None:
    tool = HTTPTool(
        allowlist_provider=_allowlist(),
        client_factory=_client_factory(lambda _req: httpx.Response(200)),
    )
    spec = tool.spec
    assert spec.name == "http"
    props = spec.parameters["properties"]
    assert set(props.keys()) == {"method", "url", "headers", "body"}
    assert spec.parameters["required"] == ["method", "url"]


def test_satisfies_tool_protocol() -> None:
    tool = HTTPTool(
        allowlist_provider=_allowlist(),
        client_factory=_client_factory(lambda _req: httpx.Response(200)),
    )
    assert isinstance(tool, Tool)
