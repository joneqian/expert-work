"""Service-account token provider for the Keycloak Admin API — Stream R (R-2).

Obtains an access token via the ``client_credentials`` grant and caches it in
process until shortly before expiry. The client secret is loaded lazily from
the Stream Q encrypted vault (a ``secret_loader`` callable) so it never lives
in settings or memory longer than a request.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx

from control_plane.keycloak.errors import (
    KeycloakAuthError,
    KeycloakUnavailableError,
)

# Refresh this many seconds before the token's stated expiry, to cover clock
# skew + the round-trip of the request the token is about to authorise.
_EXPIRY_SKEW_S = 30.0


class ServiceAccountTokenProvider:
    """Caches a service-account bearer token, refreshing it before expiry."""

    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        secret_loader: Callable[[], Awaitable[str]],
        http: httpx.AsyncClient,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = f"{base_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
        self._client_id = client_id
        self._secret_loader = secret_loader
        self._http = http
        self._now = now
        self._cached: str | None = None
        self._expires_at: float = 0.0

    async def bearer(self) -> str:
        """Return a valid access token, fetching a fresh one if the cache expired."""
        if self._cached is not None and self._now() < self._expires_at:
            return self._cached
        return await self._refresh()

    async def _refresh(self) -> str:
        secret = await self._secret_loader()
        try:
            resp = await self._http.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"token grant request failed: {exc}") from exc

        if resp.status_code in (400, 401, 403):
            # Bad client secret / disabled service account — a config error.
            raise KeycloakAuthError(f"token grant rejected: HTTP {resp.status_code}")
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"token grant 5xx: HTTP {resp.status_code}")

        payload = resp.json()
        token = payload.get("access_token")
        if not token or not isinstance(token, str):
            raise KeycloakAuthError("token grant returned no access_token")
        expires_in = float(payload.get("expires_in", 60))
        self._cached = token
        self._expires_at = self._now() + max(0.0, expires_in - _EXPIRY_SKEW_S)
        return token
