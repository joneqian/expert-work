"""Unit tests for the OA-6 lazy token refresher — Stream MCP-OAUTH.

HTTP is served by an ``httpx.MockTransport`` (no network); stores are in-memory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from control_plane.mcp_oauth_refresh import McpOAuthRefresher
from control_plane.tenant_scope import bypass_rls_session
from expert_work.persistence import (
    InMemoryMcpConnectorCatalogStore,
    InMemoryMcpOAuthConnectionStore,
)
from expert_work.protocol import (
    McpConnectorAuthSchema,
    McpConnectorCatalogUpsert,
    McpOAuthConnectionPatch,
    McpOAuthConnectionRecord,
)
from expert_work.testing import InMemorySecretStore

_NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
_MCP_URL = "https://mcp.linear.app/sse"


def _token_handler(*, status: int = 200, body: dict | None = None):  # type: ignore[no-untyped-def]
    payload = body if body is not None else {"access_token": "AT2", "expires_in": 3600}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource":
            return httpx.Response(
                200,
                json={"authorization_servers": ["https://auth.linear.app"], "resource": _MCP_URL},
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://auth.linear.app/authorize",
                    "token_endpoint": "https://auth.linear.app/token",
                },
            )
        if path == "/token":
            return httpx.Response(status, json=payload)
        return httpx.Response(404)

    return handler


def _http_factory(handler):  # type: ignore[no-untyped-def]
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


async def _seed_catalog(store: InMemoryMcpConnectorCatalogStore) -> UUID:
    async with bypass_rls_session():
        rec = await store.create(
            upsert=McpConnectorCatalogUpsert(
                name="linear",
                display_name="Linear",
                transport="sse",
                url_template=_MCP_URL,
                auth_type="oauth2",
                auth_schema=McpConnectorAuthSchema(),
                oauth_client_id="cid",
                oauth_scopes="read",
            ),
            actor_id="seed",
        )
    return rec.id


async def _seed_connection(
    oauth_store: InMemoryMcpOAuthConnectionStore,
    secret_store: InMemorySecretStore,
    *,
    tenant_id: UUID,
    user_id: str,
    catalog_id: UUID,
    expires_at: datetime | None,
    with_refresh: bool = True,
    last_error: str | None = None,
) -> McpOAuthConnectionRecord:
    rec = await oauth_store.create(
        tenant_id=tenant_id,
        user_id=user_id,
        catalog_id=catalog_id,
        name="linear",
        resolved_url=_MCP_URL,
        oauth_state="st",
        pkce_verifier="pv",
    )
    access_ref = f"secret://expert-work/tenant/{tenant_id}/mcp-oauth/{rec.id}/access"
    await secret_store.put(access_ref.removeprefix("secret://"), "AT1")
    refresh_ref: str | None = None
    if with_refresh:
        refresh_ref = f"secret://expert-work/tenant/{tenant_id}/mcp-oauth/{rec.id}/refresh"
        await secret_store.put(refresh_ref.removeprefix("secret://"), "RT1")
    return await oauth_store.update(
        connection_id=rec.id,
        tenant_id=tenant_id,
        user_id=user_id,
        patch=McpOAuthConnectionPatch(
            status="connected",
            access_token_ref=access_ref,
            refresh_token_ref=refresh_ref,
            token_expires_at=expires_at,
            last_error=last_error,
            clear_flow_state=True,
        ),
    )


def _refresher(
    oauth_store: InMemoryMcpOAuthConnectionStore,
    catalog_store: InMemoryMcpConnectorCatalogStore,
    secret_store: InMemorySecretStore,
    handler,  # type: ignore[no-untyped-def]
) -> McpOAuthRefresher:
    return McpOAuthRefresher(
        oauth_store=oauth_store,
        catalog_store=catalog_store,
        secret_store=secret_store,
        http_factory=_http_factory(handler),
        clock=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_valid_token_served_unchanged() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(hours=1),
    )
    out = await _refresher(oauth, cat, sec, _token_handler()).ensure_fresh(rec)
    assert out is not None and out.id == rec.id
    assert await sec.get(rec.access_token_ref.removeprefix("secret://")) == "AT1"  # not refreshed


@pytest.mark.asyncio
async def test_near_expiry_refreshes_in_place() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(seconds=30),  # within 60s skew
        last_error="stale",
    )
    out = await _refresher(oauth, cat, sec, _token_handler()).ensure_fresh(rec)
    assert out is not None and out.status == "connected"
    assert out.token_expires_at == _NOW + timedelta(seconds=3600)
    assert out.last_error is None  # cleared on recovery
    assert await sec.get(rec.access_token_ref.removeprefix("secret://")) == "AT2"


@pytest.mark.asyncio
async def test_refresh_rotated_refresh_token_persisted() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW - timedelta(seconds=5),
    )
    handler = _token_handler(body={"access_token": "AT2", "refresh_token": "RT2", "expires_in": 60})
    out = await _refresher(oauth, cat, sec, handler).ensure_fresh(rec)
    assert out is not None
    assert await sec.get(rec.refresh_token_ref.removeprefix("secret://")) == "RT2"


@pytest.mark.asyncio
async def test_invalid_grant_marks_revoked() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(seconds=10),
    )
    handler = _token_handler(status=400, body={"error": "invalid_grant"})
    out = await _refresher(oauth, cat, sec, handler).ensure_fresh(rec)
    assert out is None
    stored = await oauth.get(connection_id=rec.id, tenant_id=tid, user_id=uid)
    assert stored is not None and stored.status == "revoked"


@pytest.mark.asyncio
async def test_transient_failure_when_expired_marks_error() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW - timedelta(seconds=5),  # already expired
    )
    handler = _token_handler(status=503, body={})
    out = await _refresher(oauth, cat, sec, handler).ensure_fresh(rec)
    assert out is None
    stored = await oauth.get(connection_id=rec.id, tenant_id=tid, user_id=uid)
    assert stored is not None and stored.status == "error"


@pytest.mark.asyncio
async def test_transient_failure_when_still_valid_serves_current() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(seconds=30),  # near-expiry but still valid
    )
    handler = _token_handler(status=503, body={})
    out = await _refresher(oauth, cat, sec, handler).ensure_fresh(rec)
    assert out is not None and out.status == "connected"  # serve current token


@pytest.mark.asyncio
async def test_expired_without_refresh_token_marks_expired() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW - timedelta(seconds=1),
        with_refresh=False,
    )
    out = await _refresher(oauth, cat, sec, _token_handler()).ensure_fresh(rec)
    assert out is None
    stored = await oauth.get(connection_id=rec.id, tenant_id=tid, user_id=uid)
    assert stored is not None and stored.status == "expired"


@pytest.mark.asyncio
async def test_not_connected_unusable() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "u1"
    cat_id = await _seed_catalog(cat)
    rec = await oauth.create(
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        name="linear",
        resolved_url=_MCP_URL,
        oauth_state="st",
        pkce_verifier="pv",
    )  # still "pending"
    out = await _refresher(oauth, cat, sec, _token_handler()).ensure_fresh(rec)
    assert out is None


# --- cross-replica refresh lock (OA-6 hardening) ---------------------------


class _FakeRefreshLock:
    """Records acquisitions; optionally runs a hook on acquire (to simulate a
    peer replica refreshing the connection while we hold/await the lock)."""

    def __init__(self, on_acquire=None) -> None:  # type: ignore[no-untyped-def]
        self.acquired: list[tuple[UUID, str]] = []
        self._on_acquire = on_acquire

    @asynccontextmanager
    async def acquire(self, *, tenant_id: UUID, user_id: str):  # type: ignore[no-untyped-def]
        self.acquired.append((tenant_id, user_id))
        if self._on_acquire is not None:
            await self._on_acquire()
        yield


@pytest.mark.asyncio
async def test_refresh_takes_lock_then_refreshes() -> None:
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "kc-user"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(seconds=10),  # within skew → refresh
    )
    lock = _FakeRefreshLock()
    refresher = McpOAuthRefresher(
        oauth_store=oauth,
        catalog_store=cat,
        secret_store=sec,
        http_factory=_http_factory(_token_handler()),
        clock=lambda: _NOW,
        refresh_lock=lock,
    )
    out = await refresher.ensure_fresh(rec)
    assert out is not None and out.status == "connected"
    # Lock acquired once for this user; the new access token was stored.
    assert lock.acquired == [(tid, uid)]
    assert await sec.get(out.access_token_ref.removeprefix("secret://")) == "AT2"


@pytest.mark.asyncio
async def test_refresh_skips_when_peer_already_refreshed() -> None:
    """Under the lock the refresher reloads the connection; if a peer replica
    already refreshed (token now fresh), it serves that token WITHOUT a second
    refresh — the rotated-refresh-token race that would falsely revoke."""
    cat, oauth, sec = (
        InMemoryMcpConnectorCatalogStore(),
        InMemoryMcpOAuthConnectionStore(),
        InMemorySecretStore(),
    )
    tid, uid = uuid4(), "kc-user"
    cat_id = await _seed_catalog(cat)
    rec = await _seed_connection(
        oauth,
        sec,
        tenant_id=tid,
        user_id=uid,
        catalog_id=cat_id,
        expires_at=_NOW + timedelta(seconds=10),  # near-expiry on entry
    )

    async def _peer_refresh() -> None:
        # Simulate the other replica refreshing while we waited for the lock.
        await oauth.update(
            connection_id=rec.id,
            tenant_id=tid,
            user_id=uid,
            patch=McpOAuthConnectionPatch(token_expires_at=_NOW + timedelta(hours=2)),
        )

    def _no_token(request: httpx.Request) -> httpx.Response:
        assert request.url.path != "/token", "token endpoint must NOT be called"
        return _token_handler()(request)

    lock = _FakeRefreshLock(on_acquire=_peer_refresh)
    refresher = McpOAuthRefresher(
        oauth_store=oauth,
        catalog_store=cat,
        secret_store=sec,
        http_factory=_http_factory(_no_token),
        clock=lambda: _NOW,
        refresh_lock=lock,
    )
    out = await refresher.ensure_fresh(rec)
    assert out is not None and out.status == "connected"
    assert lock.acquired == [(tid, uid)]
    # Token untouched — no second refresh.
    assert await sec.get(out.access_token_ref.removeprefix("secret://")) == "AT1"
