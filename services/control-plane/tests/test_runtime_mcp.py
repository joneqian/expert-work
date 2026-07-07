"""Capability Uplift Sprint #5 — runtime MCP transport dispatcher.

Covers ``_load_mcp_server_configs`` + ``_build_mcp_client`` + the
``build_mcp_pool`` wiring with mixed-transport platform configs.

The remote transports themselves (SSE / StreamableHTTP) require a real
endpoint; their unit coverage lives in
``services/orchestrator/tests/test_mcp_tool.py`` (config validation) +
``services/control-plane/tests/test_mcp_e2e.py`` (real
``mcp-server-time``). Tests here exercise the dispatch + secret
resolution + oauth2 fail-fast in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from control_plane.runtime import (
    _build_mcp_client,
    _load_mcp_server_configs,
    build_mcp_pool,
)
from expert_work.testing import InMemorySecretStore
from orchestrator.tools import (
    MCPClient,
    MCPServerConfig,
    RecordingMCPClient,
)
from orchestrator.tools.mcp import (
    MCPOAuthNotImplementedError,
    SseMCPClient,
    StreamableHttpMCPClient,
)


def _write_config(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_load_legacy_stdio_entry_defaults_to_stdio_transport(tmp_path: Path) -> None:
    """Backward compat for operator JSON files written before Sprint #5."""
    path = _write_config(
        tmp_path,
        [{"name": "fs", "command": ["npx", "-y", "@modelcontextprotocol/server-fs"]}],
    )
    cfgs = _load_mcp_server_configs(str(path))
    assert len(cfgs) == 1
    assert cfgs[0].transport == "stdio"
    assert cfgs[0].command == ["npx", "-y", "@modelcontextprotocol/server-fs"]


def test_load_sse_entry(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        [
            {
                "name": "time",
                "transport": "sse",
                "url": "https://mcp.example.com/time/sse",
            }
        ],
    )
    cfgs = _load_mcp_server_configs(str(path))
    assert cfgs[0].transport == "sse"
    assert cfgs[0].url == "https://mcp.example.com/time/sse"


def test_load_streamable_http_with_bearer(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        [
            {
                "name": "github",
                "transport": "streamable_http",
                "url": "https://api.githubcopilot.com/mcp/",
                "auth_type": "bearer",
                "auth_config": {"token_ref": "secret://mcp/github/token"},
            }
        ],
    )
    cfgs = _load_mcp_server_configs(str(path))
    assert cfgs[0].auth_type == "bearer"
    assert cfgs[0].auth_config["token_ref"] == "secret://mcp/github/token"


def test_load_oauth2_with_client_id_and_scope(tmp_path: Path) -> None:
    """Schema accepts oauth2 configs; runtime fail-fast happens in
    ``_build_mcp_client`` (Mini-ADR U-12)."""
    path = _write_config(
        tmp_path,
        [
            {
                "name": "linear",
                "transport": "streamable_http",
                "url": "https://mcp.linear.app/",
                "auth_type": "oauth2",
                "auth_config": {"client_id": "expert-work", "scope": "read"},
            }
        ],
    )
    cfgs = _load_mcp_server_configs(str(path))
    assert cfgs[0].auth_type == "oauth2"


@pytest.mark.asyncio
async def test_build_client_oauth2_fails_fast() -> None:
    cfg = MCPServerConfig(
        name="linear",
        transport="streamable_http",
        url="https://mcp.linear.app/",
        auth_type="oauth2",
        auth_config={"client_id": "x", "scope": "read"},
    )
    with pytest.raises(MCPOAuthNotImplementedError, match=r"L.L8-MCP"):
        await _build_mcp_client(cfg, secret_store=None)


@pytest.mark.asyncio
async def test_build_client_revalidates_url_at_runtime() -> None:
    """Audit #4: the runtime connect-out re-validates the URL so a private /
    metadata target (or any row that reached the DB unvalidated) cannot be
    dialed — registration, probe, and runtime share one gate."""
    from expert_work.common.url_validation import RemoteURLError

    cfg = MCPServerConfig(
        name="internal",
        transport="streamable_http",
        url="http://169.254.169.254/latest/meta-data/",
        auth_type="none",
    )
    with pytest.raises(RemoteURLError):
        await _build_mcp_client(cfg, secret_store=None)


@pytest.mark.asyncio
async def test_build_client_bearer_resolves_token_via_secret_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_mcp_client`` should inject ``Authorization: Bearer <token>``
    by resolving ``token_ref`` through the SecretStore (Mini-ADR U-11)."""
    store = InMemorySecretStore()
    await store.put("mcp/github/token", "TOKEN-XYZ")
    cfg = MCPServerConfig(
        name="github",
        transport="streamable_http",
        url="https://api.example.com/mcp",
        auth_type="bearer",
        auth_config={"token_ref": "secret://mcp/github/token"},
    )

    captured: dict[str, Any] = {}

    async def fake_start(self: Any) -> None:
        captured["resolved_headers"] = dict(self.resolved_headers)
        # Skip the real SDK round-trip.

    monkeypatch.setattr(StreamableHttpMCPClient, "start", fake_start)
    client = await _build_mcp_client(cfg, secret_store=store)
    assert isinstance(client, StreamableHttpMCPClient)
    assert captured["resolved_headers"]["Authorization"] == "Bearer TOKEN-XYZ"


@pytest.mark.asyncio
async def test_build_client_resolves_custom_headers_via_secret_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1: a ``headers_ref`` blob is decrypted and merged into resolved
    headers (so a server can require e.g. ``X-API-Key``)."""
    store = InMemorySecretStore()
    await store.put("mcp/svc/headers", json.dumps({"X-API-Key": "KEY-123"}))
    cfg = MCPServerConfig(
        name="svc",
        transport="streamable_http",
        url="https://api.example.com/mcp",
        auth_type="none",
        auth_config={"headers_ref": "secret://mcp/svc/headers"},
    )

    captured: dict[str, Any] = {}

    async def fake_start(self: Any) -> None:
        captured["resolved_headers"] = dict(self.resolved_headers)

    monkeypatch.setattr(StreamableHttpMCPClient, "start", fake_start)
    await _build_mcp_client(cfg, secret_store=store)
    assert captured["resolved_headers"]["X-API-Key"] == "KEY-123"


@pytest.mark.asyncio
async def test_build_client_bearer_overrides_custom_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1 coexistence: bearer is merged AFTER custom headers, so a stray
    custom ``Authorization`` can never shadow the bearer token (defence in
    depth — the API layer also rejects this combination)."""
    store = InMemorySecretStore()
    await store.put("mcp/svc/headers", json.dumps({"Authorization": "Bearer SNEAKY"}))
    await store.put("mcp/svc/token", "REAL-TOKEN")
    cfg = MCPServerConfig(
        name="svc",
        transport="streamable_http",
        url="https://api.example.com/mcp",
        auth_type="bearer",
        auth_config={
            "headers_ref": "secret://mcp/svc/headers",
            "token_ref": "secret://mcp/svc/token",
        },
    )

    captured: dict[str, Any] = {}

    async def fake_start(self: Any) -> None:
        captured["resolved_headers"] = dict(self.resolved_headers)

    monkeypatch.setattr(StreamableHttpMCPClient, "start", fake_start)
    await _build_mcp_client(cfg, secret_store=store)
    assert captured["resolved_headers"]["Authorization"] == "Bearer REAL-TOKEN"


@pytest.mark.asyncio
async def test_build_client_bearer_without_secret_store_raises() -> None:
    cfg = MCPServerConfig(
        name="github",
        transport="streamable_http",
        url="https://api.example.com/mcp",
        auth_type="bearer",
        auth_config={"token_ref": "secret://mcp/github/token"},
    )
    with pytest.raises(RuntimeError, match="SecretStore"):
        await _build_mcp_client(cfg, secret_store=None)


@pytest.mark.asyncio
async def test_build_client_sse_picks_sse_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_start(self: Any) -> None:
        return None

    monkeypatch.setattr(SseMCPClient, "start", noop_start)
    cfg = MCPServerConfig(
        name="time",
        transport="sse",
        url="https://mcp.example.com/sse",
    )
    client = await _build_mcp_client(cfg, secret_store=None)
    assert isinstance(client, SseMCPClient)


@pytest.mark.asyncio
async def test_build_mcp_pool_accepts_secret_store_kwarg() -> None:
    """build_mcp_pool with None config_file should be a no-op and accept
    the new ``secret_store`` kwarg without complaint."""
    async with build_mcp_pool(None, secret_store=InMemorySecretStore()) as pool:
        assert pool.names() == []


@pytest.mark.asyncio
async def test_build_mcp_pool_with_injected_factory() -> None:
    """Tests can inject a fake factory that returns a RecordingMCPClient
    (Stream E.9 pattern — preserved by Sprint #5 dispatcher refactor)."""

    async def fake_factory(cfg: MCPServerConfig) -> MCPClient:
        return RecordingMCPClient()

    tmp = Path(__file__).parent / "_test_mcp_pool.json"
    try:
        tmp.write_text(json.dumps([{"name": "fake", "command": ["x"]}]), encoding="utf-8")
        async with build_mcp_pool(str(tmp), client_factory=fake_factory) as pool:
            assert pool.names() == ["fake"]
    finally:
        tmp.unlink(missing_ok=True)
