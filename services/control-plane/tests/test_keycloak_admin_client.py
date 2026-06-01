"""Unit tests for the Keycloak Admin client — Stream R (R-1/R-2).

Uses ``httpx.MockTransport`` so no live Keycloak is needed. Covers the token
cache, 409→exists mapping, transport→unavailable mapping, and the fake double.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from control_plane.keycloak import (
    FakeKeycloakAdminClient,
    HttpKeycloakAdminClient,
    KeycloakAuthError,
    KeycloakUnavailableError,
    KeycloakUserExistsError,
    ServiceAccountTokenProvider,
)

_BASE = "http://kc:8080"
_REALM = "helix-agent"


def _token_handler(calls: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 300})
        raise AssertionError(f"unexpected {request.url}")

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_token_cache_reuses_until_expiry() -> None:
    calls: list[str] = []
    fake_clock = [1000.0]
    async with httpx.AsyncClient(transport=_token_handler(calls)) as http:
        provider = ServiceAccountTokenProvider(
            base_url=_BASE,
            realm=_REALM,
            client_id="helix-agent-api-internal",
            secret_loader=_const_secret("sek"),
            http=http,
            now=lambda: fake_clock[0],
        )
        assert await provider.bearer() == "tok-1"
        assert await provider.bearer() == "tok-1"  # cached — no second grant
        assert len(calls) == 1
        # Advance past expiry (300 - 30 skew = 270s window).
        fake_clock[0] += 271
        await provider.bearer()
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_token_grant_rejected_is_auth_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = ServiceAccountTokenProvider(
            base_url=_BASE,
            realm=_REALM,
            client_id="c",
            secret_loader=_const_secret("bad"),
            http=http,
        )
        with pytest.raises(KeycloakAuthError):
            await provider.bearer()


@pytest.mark.asyncio
async def test_create_user_returns_location_id() -> None:
    new_id = str(uuid4())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        if request.method == "POST" and request.url.path.endswith("/users"):
            return httpx.Response(
                201, headers={"Location": f"{_BASE}/admin/realms/{_REALM}/users/{new_id}"}
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    client = _http_client(handle)
    async with client[0]:
        user = await client[1].create_user(
            email="eng@co.com", tenant_id=uuid4(), display_name="Eng One"
        )
        assert user.id == new_id
        assert user.email == "eng@co.com"
        assert user.enabled is True


@pytest.mark.asyncio
async def test_create_user_409_maps_to_exists() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(409, json={"errorMessage": "User exists"})

    client = _http_client(handle)
    async with client[0]:
        with pytest.raises(KeycloakUserExistsError):
            await client[1].create_user(email="dup@co.com", tenant_id=uuid4(), display_name=None)


@pytest.mark.asyncio
async def test_create_user_5xx_maps_to_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(503)

    client = _http_client(handle)
    async with client[0]:
        with pytest.raises(KeycloakUnavailableError):
            await client[1].create_user(email="e@co.com", tenant_id=uuid4(), display_name=None)


@pytest.mark.asyncio
async def test_delete_user_404_is_idempotent() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(404)

    client = _http_client(handle)
    async with client[0]:
        await client[1].delete_user(user_id="gone")  # no raise


@pytest.mark.asyncio
async def test_send_setup_email_posts_actions() -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        if request.url.path.endswith("/execute-actions-email"):
            captured["lifespan"] = request.url.params.get("lifespan")
            captured["body"] = request.read()
            return httpx.Response(204)
        raise AssertionError(f"unexpected {request.url}")

    client = _http_client(handle)
    async with client[0]:
        await client[1].send_setup_email(user_id="u1", lifespan_s=3600)
        assert captured["lifespan"] == "3600"
        assert b"UPDATE_PASSWORD" in captured["body"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_fake_client_full_flow() -> None:
    fake = FakeKeycloakAdminClient()
    tenant = uuid4()
    u = await fake.create_user(email="x@co.com", tenant_id=tenant, display_name="X")
    assert u.id in fake.users
    await fake.send_setup_email(user_id=u.id, lifespan_s=60)
    assert fake.users[u.id].emails_sent == 1
    # Duplicate email rejected.
    with pytest.raises(KeycloakUserExistsError):
        await fake.create_user(email="X@co.com", tenant_id=tenant, display_name=None)
    await fake.set_enabled(user_id=u.id, enabled=False)
    assert fake.users[u.id].user.enabled is False
    await fake.delete_user(user_id=u.id)
    assert u.id not in fake.users


# ----------------------------------------------------------------- helpers


def _const_secret(value: str):
    async def _loader() -> str:
        return value

    return _loader


def _http_client(
    handle,
) -> tuple[httpx.AsyncClient, HttpKeycloakAdminClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = ServiceAccountTokenProvider(
        base_url=_BASE,
        realm=_REALM,
        client_id="helix-agent-api-internal",
        secret_loader=_const_secret("sek"),
        http=http,
    )
    client = HttpKeycloakAdminClient(
        base_url=_BASE, realm=_REALM, token_provider=provider, http=http
    )
    return http, client
