"""MCP OAuth 2.1 engine — Stream MCP-OAUTH (OA-2).

Pure logic for the per-user MCP authorization flow, mandated by the MCP
authorization spec (OAuth 2.1 + PKCE + metadata discovery):

1. ``discover_oauth_metadata`` — RFC 9728 Protected Resource Metadata
   (``<mcp>/.well-known/oauth-protected-resource``) → RFC 8414 Authorization
   Server Metadata (``<as>/.well-known/oauth-authorization-server``), yielding
   the authorize/token endpoints + the ``resource`` identifier (RFC 8707).
2. ``generate_pkce`` / ``generate_state`` — PKCE S256 + CSRF state.
3. ``build_authorize_url`` — the browser redirect URL.
4. ``exchange_code`` / ``refresh_token`` — token endpoint round-trips.

No DB / no FastAPI here — the OA-3 endpoints drive this. ``httpx.AsyncClient``
is injected so tests use a ``MockTransport`` (no real network). Every
discovered URL is re-validated (SSRF) before connect-out, and responses are
size-capped (a malicious/buggy AS must not exhaust the control plane).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from helix_agent.common.url_validation import RemoteURLError, validate_remote_url

# Defensive cap on discovery / token JSON bodies (audit #5 spirit): a hostile or
# buggy authorization server must not stream an unbounded body in-process.
_MAX_RESPONSE_BYTES = 256 * 1024
_DEFAULT_TIMEOUT_S = 15.0
# PKCE verifier length (RFC 7636 allows 43-128 chars; token_urlsafe(64) ≈ 86).
_PKCE_VERIFIER_BYTES = 64
_STATE_BYTES = 32


class McpOAuthError(Exception):
    """An MCP OAuth step failed. ``code`` is a stable machine token; ``message``
    is scrubbed (never carries a token / authorization code)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OAuthServerMetadata:
    """Discovered endpoints for an MCP connector's authorization server."""

    authorization_endpoint: str
    token_endpoint: str
    resource: str
    scopes_supported: tuple[str, ...] = ()


@dataclass(frozen=True)
class PkcePair:
    verifier: str = field(repr=False)
    challenge: str


@dataclass(frozen=True)
class TokenResponse:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_in: int | None = None
    scope: str | None = None


def generate_pkce() -> PkcePair:
    """RFC 7636 S256 PKCE pair."""
    verifier = secrets.token_urlsafe(_PKCE_VERIFIER_BYTES)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return PkcePair(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    """Opaque CSRF state token for the authorize → callback round-trip."""
    return secrets.token_urlsafe(_STATE_BYTES)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _get_json(http: httpx.AsyncClient, url: str, *, what: str) -> dict[str, Any]:
    try:
        validate_remote_url(url)
    except RemoteURLError as exc:
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", f"{what}: invalid URL") from exc
    try:
        resp = await http.get(url)
    except httpx.HTTPError as exc:
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", f"{what}: request failed") from exc
    if resp.status_code != 200:
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", f"{what}: status {resp.status_code}")
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", f"{what}: response too large")
    try:
        data = resp.json()
    except ValueError as exc:
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", f"{what}: invalid JSON") from exc
    if not isinstance(data, dict):
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", f"{what}: not an object")
    return data


async def discover_oauth_metadata(*, mcp_url: str, http: httpx.AsyncClient) -> OAuthServerMetadata:
    """Discover the authorization server for an MCP connector (RFC 9728 → 8414)."""
    prm_url = f"{_origin(mcp_url)}/.well-known/oauth-protected-resource"
    prm = await _get_json(http, prm_url, what="protected_resource_metadata")
    servers = prm.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", "PRM has no authorization_servers")
    resource = prm.get("resource")
    if not isinstance(resource, str) or not resource:
        resource = mcp_url

    as_url = f"{_origin(servers[0])}/.well-known/oauth-authorization-server"
    meta = await _get_json(http, as_url, what="authorization_server_metadata")
    authorize = meta.get("authorization_endpoint")
    token = meta.get("token_endpoint")
    if not isinstance(authorize, str) or not isinstance(token, str):
        raise McpOAuthError("MCP_OAUTH_DISCOVERY_FAILED", "AS metadata missing endpoints")
    for endpoint in (authorize, token):
        try:
            validate_remote_url(endpoint)
        except RemoteURLError as exc:
            raise McpOAuthError(
                "MCP_OAUTH_DISCOVERY_FAILED", "AS endpoint failed URL validation"
            ) from exc
    scopes_raw = meta.get("scopes_supported")
    scopes = (
        tuple(s for s in scopes_raw if isinstance(s, str)) if isinstance(scopes_raw, list) else ()
    )
    return OAuthServerMetadata(
        authorization_endpoint=authorize,
        token_endpoint=token,
        resource=resource,
        scopes_supported=scopes,
    )


def build_authorize_url(
    *,
    metadata: OAuthServerMetadata,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    pkce_challenge: str,
) -> str:
    """Build the browser authorize redirect (OAuth 2.1 + PKCE + RFC 8707)."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce_challenge,
        "code_challenge_method": "S256",
        "resource": metadata.resource,
    }
    if scopes:
        params["scope"] = scopes
    sep = "&" if urlparse(metadata.authorization_endpoint).query else "?"
    return f"{metadata.authorization_endpoint}{sep}{urlencode(params)}"


async def _post_token(
    http: httpx.AsyncClient, *, token_endpoint: str, form: dict[str, str]
) -> TokenResponse:
    try:
        validate_remote_url(token_endpoint)
    except RemoteURLError as exc:
        raise McpOAuthError("MCP_OAUTH_TOKEN_FAILED", "invalid token endpoint") from exc
    try:
        resp = await http.post(token_endpoint, data=form)
    except httpx.HTTPError as exc:
        raise McpOAuthError("MCP_OAUTH_TOKEN_FAILED", "token request failed") from exc
    if resp.status_code != 200:
        raise McpOAuthError("MCP_OAUTH_TOKEN_FAILED", f"token status {resp.status_code}")
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise McpOAuthError("MCP_OAUTH_TOKEN_FAILED", "token response too large")
    try:
        data = resp.json()
    except ValueError as exc:
        raise McpOAuthError("MCP_OAUTH_TOKEN_FAILED", "invalid token JSON") from exc
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise McpOAuthError("MCP_OAUTH_TOKEN_FAILED", "no access_token in response")
    refresh = data.get("refresh_token")
    expires = data.get("expires_in")
    scope = data.get("scope")
    return TokenResponse(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) else None,
        expires_in=expires if isinstance(expires, int) else None,
        scope=scope if isinstance(scope, str) else None,
    )


async def exchange_code(
    *,
    metadata: OAuthServerMetadata,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    http: httpx.AsyncClient,
) -> TokenResponse:
    """Exchange an authorization code for tokens (PKCE + RFC 8707 resource)."""
    return await _post_token(
        http,
        token_endpoint=metadata.token_endpoint,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
            "resource": metadata.resource,
        },
    )


async def refresh_token(
    *,
    metadata: OAuthServerMetadata,
    client_id: str,
    refresh_token: str,
    http: httpx.AsyncClient,
) -> TokenResponse:
    """Refresh an access token using a refresh token."""
    return await _post_token(
        http,
        token_endpoint=metadata.token_endpoint,
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": metadata.resource,
        },
    )


def default_http_client() -> httpx.AsyncClient:
    """Production HTTP client with a bounded timeout (OA-3 wiring uses this)."""
    return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S, follow_redirects=False)
