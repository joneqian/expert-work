"""Unit tests for the remote MCP connect-probe (Stream V-C)."""

from __future__ import annotations

import pytest

from control_plane.mcp_probe import McpProbeError, probe_remote_mcp
from orchestrator.tools.mcp import MCPToolDef


class _FakeClient:
    def __init__(
        self,
        *,
        tools=None,
        raise_on_start=None,
        raise_on_list=None,
        raise_on_close=None,
    ):
        self._tools = tools or []
        self._raise_on_start = raise_on_start
        self._raise_on_list = raise_on_list
        self._raise_on_close = raise_on_close
        self.closed = False

    async def start(self) -> None:
        if self._raise_on_start is not None:
            raise self._raise_on_start

    async def list_tools(self):
        if self._raise_on_list is not None:
            raise self._raise_on_list
        return tuple(self._tools)

    async def close(self) -> None:
        self.closed = True
        if self._raise_on_close is not None:
            raise self._raise_on_close


@pytest.mark.asyncio
async def test_probe_returns_tools_on_success() -> None:
    captured: dict[str, object] = {}

    def factory(config, headers):
        captured["transport"] = config.transport
        captured["headers"] = headers
        return _FakeClient(tools=[MCPToolDef(name="create_issue", description="", input_schema={})])

    tools = await probe_remote_mcp(
        name="github",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        bearer_token="ghp_secret",
        timeout_s=10.0,
        client_factory=factory,
    )
    assert [t.name for t in tools] == ["create_issue"]
    assert captured["headers"]["Authorization"] == "Bearer ghp_secret"


@pytest.mark.asyncio
async def test_probe_no_auth_sends_no_authorization_header() -> None:
    captured: dict[str, object] = {}

    def factory(config, headers):
        captured["headers"] = headers
        return _FakeClient(tools=[])

    await probe_remote_mcp(
        name="open",
        transport="sse",
        url="https://mcp.example.com/sse",
        bearer_token=None,
        timeout_s=10.0,
        client_factory=factory,
    )
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_probe_rejects_ssrf_url() -> None:
    with pytest.raises(McpProbeError) as ei:
        await probe_remote_mcp(
            name="evil",
            transport="streamable_http",
            url="http://169.254.169.254/latest",
            bearer_token=None,
            timeout_s=10.0,
            client_factory=lambda c, h: _FakeClient(),
        )
    assert ei.value.code == "MCP_SERVER_INVALID_URL"


@pytest.mark.asyncio
async def test_probe_wraps_connect_failure() -> None:
    def factory(config, headers):
        return _FakeClient(raise_on_start=RuntimeError("connection refused"))

    with pytest.raises(McpProbeError) as ei:
        await probe_remote_mcp(
            name="down",
            transport="streamable_http",
            url="https://down.example.com/mcp",
            bearer_token=None,
            timeout_s=10.0,
            client_factory=factory,
        )
    assert ei.value.code == "MCP_SERVER_PROBE_FAILED"


@pytest.mark.asyncio
async def test_probe_always_closes_client() -> None:
    client = _FakeClient(raise_on_list=RuntimeError("boom"))

    def factory(config, headers):
        return client

    with pytest.raises(McpProbeError):
        await probe_remote_mcp(
            name="x",
            transport="sse",
            url="https://x.example.com/sse",
            bearer_token=None,
            timeout_s=10.0,
            client_factory=factory,
        )
    assert client.closed is True


@pytest.mark.asyncio
async def test_probe_always_closes_client_on_start_failure() -> None:
    client = _FakeClient(raise_on_start=RuntimeError("refused"))
    with pytest.raises(McpProbeError):
        await probe_remote_mcp(
            name="x",
            transport="sse",
            url="https://x.example.com/sse",
            bearer_token=None,
            timeout_s=10.0,
            client_factory=lambda c, h: client,
        )
    assert client.closed is True


@pytest.mark.asyncio
async def test_probe_error_not_masked_when_close_raises() -> None:
    client = _FakeClient(
        raise_on_list=RuntimeError("list failed"),
        raise_on_close=RuntimeError("close failed"),
    )
    with pytest.raises(McpProbeError) as ei:
        await probe_remote_mcp(
            name="x",
            transport="sse",
            url="https://x.example.com/sse",
            bearer_token=None,
            timeout_s=10.0,
            client_factory=lambda c, h: client,
        )
    assert ei.value.code == "MCP_SERVER_PROBE_FAILED"
