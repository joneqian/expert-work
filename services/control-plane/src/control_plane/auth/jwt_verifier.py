"""RS256 JWT verifier + JWKS providers — Stream C.1.

Three pieces:

1. :class:`JWKSProvider` — Protocol for fetching a :class:`jwt.PyJWK` by
   key id. Two implementations:

   * :class:`HTTPJWKSProvider` — production path; fetches from
     ``{oidc_issuer}/protocol/openid-connect/certs`` (Keycloak default)
     with a per-process TTL cache. Missing ``kid`` triggers a one-shot
     refresh (so newly-rotated Keycloak keys are picked up without a
     restart).
   * :class:`StaticJWKSProvider` — test path; in-memory keyset, never
     fetches over the network.

2. :class:`JWTVerifier` — the only public verification entrypoint. Takes a
   bearer token string, returns a :class:`JWTClaims` value object. Never
   raises ``jwt.*`` exceptions outside the module — all errors map to
   :mod:`control_plane.auth.errors`.

3. Claim extraction helpers — translate Keycloak's realm/client role
   layout to our flat ``roles`` tuple.

ADR C-1 / ADR C-2 (see ``docs/streams/STREAM-C-DESIGN.md``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from time import monotonic
from typing import Any, Protocol
from uuid import UUID

import httpx
import jwt
from jwt import PyJWK

from control_plane.auth.errors import (
    AuthBackendUnavailableError,
    InvalidTokenError,
    TokenExpiredError,
)
from helix_agent.protocol.auth import JWTClaims, SubjectType

logger = logging.getLogger("helix.control_plane.auth.jwt")


class JWKSProvider(Protocol):
    """Strategy interface for resolving a key id (``kid``) to a PyJWK."""

    async def get_key(self, kid: str) -> PyJWK:  # pragma: no cover - Protocol
        """Return the key for ``kid``; raise :class:`InvalidTokenError` if unknown."""


class StaticJWKSProvider:
    """In-memory JWKS — built directly from one or more PyJWK objects."""

    def __init__(self, keys: dict[str, PyJWK]) -> None:
        if not keys:
            msg = "StaticJWKSProvider requires at least one key"
            raise ValueError(msg)
        self._keys = dict(keys)

    async def get_key(self, kid: str) -> PyJWK:
        try:
            return self._keys[kid]
        except KeyError:
            raise InvalidTokenError() from None


class HTTPJWKSProvider:
    """Fetches JWKS over HTTP with per-process TTL cache + miss-driven refresh.

    Concurrency: a single ``asyncio.Lock`` ensures concurrent cache misses
    coalesce into one upstream request (avoids thundering-herd on Keycloak
    during key rotation).
    """

    def __init__(
        self,
        jwks_uri: str,
        *,
        cache_ttl_s: float = 300.0,
        request_timeout_s: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if cache_ttl_s <= 0:
            msg = "cache_ttl_s must be > 0"
            raise ValueError(msg)
        self._jwks_uri = jwks_uri
        self._cache_ttl_s = cache_ttl_s
        self._timeout_s = request_timeout_s
        self._client = client
        self._owns_client = client is None
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def get_key(self, kid: str) -> PyJWK:
        # Cache hit fast-path.
        if self._is_fresh() and kid in self._keys:
            return self._keys[kid]

        async with self._lock:
            # Re-check after lock acquisition — another waiter may have refreshed.
            if self._is_fresh() and kid in self._keys:
                return self._keys[kid]
            await self._refresh()
            if kid not in self._keys:
                # Unknown kid even after a fresh fetch → reject (rather than
                # loop on bogus tokens).
                raise InvalidTokenError()
            return self._keys[kid]

    def _is_fresh(self) -> bool:
        return bool(self._keys) and (monotonic() - self._fetched_at) < self._cache_ttl_s

    async def _refresh(self) -> None:
        client = self._client or httpx.AsyncClient(timeout=self._timeout_s)
        try:
            try:
                response = await client.get(self._jwks_uri, timeout=self._timeout_s)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                logger.warning(
                    "jwks.fetch_failed",
                    extra={"jwks_uri": self._jwks_uri, "error_type": type(exc).__name__},
                )
                raise AuthBackendUnavailableError() from None
        finally:
            if self._client is None:
                await client.aclose()

        keys_section = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(keys_section, list) or not keys_section:
            logger.warning("jwks.fetch_empty", extra={"jwks_uri": self._jwks_uri})
            raise AuthBackendUnavailableError()

        new_keys: dict[str, PyJWK] = {}
        for entry in keys_section:
            try:
                pyjwk = PyJWK.from_dict(entry)
            except jwt.PyJWKError:  # malformed entry — skip
                continue
            kid = entry.get("kid") if isinstance(entry, dict) else None
            if isinstance(kid, str):
                new_keys[kid] = pyjwk
        if not new_keys:
            raise AuthBackendUnavailableError()
        self._keys = new_keys
        self._fetched_at = monotonic()


class JWTVerifier:
    """Verify RS256 JWTs against a :class:`JWKSProvider`."""

    def __init__(
        self,
        *,
        jwks_provider: JWKSProvider,
        issuer: str,
        audience: Sequence[str],
        leeway_s: int = 30,
    ) -> None:
        if not issuer:
            msg = "issuer must be a non-empty string"
            raise ValueError(msg)
        if not audience:
            msg = "audience must contain at least one entry"
            raise ValueError(msg)
        self._jwks_provider = jwks_provider
        self._issuer = issuer
        self._audience = tuple(audience)
        self._leeway_s = leeway_s

    async def verify(self, token: str) -> JWTClaims:
        """Verify ``token``; return :class:`JWTClaims`. Failures raise typed errors."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.DecodeError:
            raise InvalidTokenError() from None
        kid = header.get("kid") if isinstance(header, dict) else None
        if not isinstance(kid, str):
            raise InvalidTokenError()

        pyjwk = await self._jwks_provider.get_key(kid)

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                pyjwk.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=list(self._audience),
                leeway=self._leeway_s,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError() from None
        except (jwt.InvalidTokenError, jwt.DecodeError):
            # All other PyJWT validation errors collapse to one public message —
            # downstream cannot distinguish (intentional; CodeQL stack-trace-exposure).
            raise InvalidTokenError() from None

        return _claims_from_payload(payload)


def _claims_from_payload(payload: dict[str, Any]) -> JWTClaims:
    """Convert a verified JWT payload to :class:`JWTClaims`."""
    aud_raw = payload.get("aud")
    aud_tuple: tuple[str, ...]
    if isinstance(aud_raw, str):
        aud_tuple = (aud_raw,)
    elif isinstance(aud_raw, list | tuple):
        aud_tuple = tuple(str(a) for a in aud_raw)
    else:
        raise InvalidTokenError()

    try:
        tenant_id = UUID(str(payload.get("tenant_id")))
    except (TypeError, ValueError):
        raise InvalidTokenError() from None

    sub_type_raw = payload.get("sub_type", "user")
    sub_type: SubjectType = "service_account" if sub_type_raw == "service_account" else "user"

    roles = _coerce_str_tuple(payload.get("roles"))
    # Keycloak realm_access.roles is the canonical place for realm roles;
    # we accept both top-level ``roles`` (our convention) and Keycloak's
    # nested format for compatibility.
    if not roles:
        realm_access = payload.get("realm_access")
        if isinstance(realm_access, dict):
            roles = _coerce_str_tuple(realm_access.get("roles"))

    scopes_raw = payload.get("scope") or payload.get("scopes")
    scopes: tuple[str, ...]
    if isinstance(scopes_raw, str):
        scopes = tuple(s for s in scopes_raw.split() if s)
    else:
        scopes = _coerce_str_tuple(scopes_raw)

    allowed_tenants_raw = payload.get("allowed_tenants")
    allowed_tenants: tuple[UUID, ...] = ()
    if isinstance(allowed_tenants_raw, list | tuple):
        parsed: list[UUID] = []
        for value in allowed_tenants_raw:
            try:
                parsed.append(UUID(str(value)))
            except (TypeError, ValueError):
                continue
        allowed_tenants = tuple(parsed)

    iat_raw = payload.get("iat")
    iat = int(iat_raw) if isinstance(iat_raw, int | float) else None
    jti_raw = payload.get("jti")
    jti = str(jti_raw) if isinstance(jti_raw, str) else None

    email_raw = payload.get("email")
    email = email_raw if isinstance(email_raw, str) and email_raw else None
    email_verified = payload.get("email_verified") is True

    return JWTClaims(
        iss=str(payload["iss"]),
        sub=str(payload["sub"]),
        aud=aud_tuple,
        exp=int(payload["exp"]),
        iat=iat,
        jti=jti,
        tenant_id=tenant_id,
        sub_type=sub_type,
        roles=roles,
        scopes=scopes,
        allowed_tenants=allowed_tenants,
        email=email,
        email_verified=email_verified,
    )


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value if isinstance(v, str))
    return ()
