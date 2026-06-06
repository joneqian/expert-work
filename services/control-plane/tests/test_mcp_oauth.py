"""Unit tests for the MCP OAuth 2.1 engine — Stream MCP-OAUTH (OA-2).

All HTTP is served by an ``httpx.MockTransport`` — no real network.
"""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from control_plane.mcp_oauth import (
    McpOAuthError,
    OAuthServerMetadata,
    build_authorize_url,
    discover_oauth_metadata,
    exchange_code,
    generate_pkce,
    generate_state,
    refresh_token,
)

_MCP_URL = "https://mcp.test/sse"
_META = OAuthServerMetadata(
    authorization_endpoint="https://auth.test/authorize",
    token_endpoint="https://auth.test/token",
    resource=_MCP_URL,
    scopes_supported=("read", "write"),
)


def _client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- PKCE / state ----------------------------------------------------------


def test_generate_pkce_is_s256() -> None:
    pair = generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert pair.challenge == expected
    assert pair.verifier != pair.challenge


def test_generate_state_unique() -> None:
    assert generate_state() != generate_state()


def test_pkce_verifier_not_in_repr() -> None:
    pair = generate_pkce()
    assert pair.verifier not in repr(pair)


# --- authorize URL ---------------------------------------------------------


def test_build_authorize_url_has_pkce_and_resource() -> None:
    url = build_authorize_url(
        metadata=_META,
        client_id="cid",
        redirect_uri="https://helix.test/cb",
        scopes="read write",
        state="st-1",
        pkce_challenge="chal",
    )
    assert url.startswith("https://auth.test/authorize?")
    assert "response_type=code" in url
    assert "code_challenge=chal" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=cid" in url
    assert "state=st-1" in url
    # RFC 8707 resource indicator points at the MCP server.
    assert "resource=https%3A%2F%2Fmcp.test%2Fsse" in url


# --- discovery -------------------------------------------------------------


def _discovery_handler(*, token_endpoint: str = "https://auth.test/token"):  # type: ignore[no-untyped-def] # noqa: S107
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={
                    "authorization_servers": ["https://auth.test"],
                    "resource": _MCP_URL,
                },
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://auth.test/authorize",
                    "token_endpoint": token_endpoint,
                    "scopes_supported": ["read", "write"],
                },
            )
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_discover_resolves_endpoints() -> None:
    async with _client(_discovery_handler()) as http:
        meta = await discover_oauth_metadata(mcp_url=_MCP_URL, http=http)
    assert meta.authorization_endpoint == "https://auth.test/authorize"
    assert meta.token_endpoint == "https://auth.test/token"
    assert meta.resource == _MCP_URL
    assert meta.scopes_supported == ("read", "write")


@pytest.mark.asyncio
async def test_discover_missing_authorization_servers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resource": _MCP_URL})

    async with _client(handler) as http:
        with pytest.raises(McpOAuthError) as exc:
            await discover_oauth_metadata(mcp_url=_MCP_URL, http=http)
    assert exc.value.code == "MCP_OAUTH_DISCOVERY_FAILED"


@pytest.mark.asyncio
async def test_discover_rejects_private_token_endpoint() -> None:
    """SSRF: an AS metadata pointing the token endpoint at a private IP is rejected."""
    async with _client(_discovery_handler(token_endpoint="http://169.254.169.254/token")) as http:
        with pytest.raises(McpOAuthError) as exc:
            await discover_oauth_metadata(mcp_url=_MCP_URL, http=http)
    assert exc.value.code == "MCP_OAUTH_DISCOVERY_FAILED"


# --- token exchange / refresh ---------------------------------------------


def _token_handler(status: int = 200, body: dict | None = None):  # type: ignore[no-untyped-def]
    payload = (
        body
        if body is not None
        else {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "scope": "read",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(status, json=payload)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_exchange_code_parses_tokens() -> None:
    async with _client(_token_handler()) as http:
        tok = await exchange_code(
            metadata=_META,
            client_id="cid",
            code="authcode",
            code_verifier="verifier",
            redirect_uri="https://helix.test/cb",
            http=http,
        )
    assert tok.access_token == "AT"
    assert tok.refresh_token == "RT"
    assert tok.expires_in == 3600
    assert tok.scope == "read"


@pytest.mark.asyncio
async def test_exchange_code_error_status_raises() -> None:
    async with _client(_token_handler(status=400, body={"error": "invalid_grant"})) as http:
        with pytest.raises(McpOAuthError) as exc:
            await exchange_code(
                metadata=_META,
                client_id="cid",
                code="bad",
                code_verifier="v",
                redirect_uri="https://helix.test/cb",
                http=http,
            )
    assert exc.value.code == "MCP_OAUTH_TOKEN_FAILED"


@pytest.mark.asyncio
async def test_exchange_code_missing_access_token_raises() -> None:
    async with _client(_token_handler(body={"refresh_token": "RT"})) as http:
        with pytest.raises(McpOAuthError):
            await exchange_code(
                metadata=_META,
                client_id="cid",
                code="c",
                code_verifier="v",
                redirect_uri="https://helix.test/cb",
                http=http,
            )


@pytest.mark.asyncio
async def test_refresh_token_returns_new_access() -> None:
    async with _client(_token_handler(body={"access_token": "AT2", "expires_in": 60})) as http:
        tok = await refresh_token(metadata=_META, client_id="cid", refresh_token="RT", http=http)
    assert tok.access_token == "AT2"
    assert tok.expires_in == 60


@pytest.mark.asyncio
async def test_access_token_not_in_repr() -> None:
    async with _client(_token_handler()) as http:
        tok = await exchange_code(
            metadata=_META,
            client_id="cid",
            code="c",
            code_verifier="v",
            redirect_uri="https://helix.test/cb",
            http=http,
        )
    assert "AT" not in repr(tok)
