"""Keycloak Admin REST client — Stream R (Mini-ADR R-1/R-3).

expert_work provisions member accounts directly in Keycloak (the single IdP) rather
than self-managing passwords: ``create_user`` + a native
``execute-actions-email`` set-password link. The ``Protocol`` keeps the
orchestration testable against a fake; ``HttpKeycloakAdminClient`` is the live
implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx

from control_plane.keycloak.errors import (
    KeycloakUnavailableError,
    KeycloakUserExistsError,
)
from control_plane.keycloak.token import ServiceAccountTokenProvider


@dataclass(frozen=True)
class KeycloakUser:
    """A provisioned Keycloak account (the bits expert_work needs back)."""

    id: str
    username: str
    email: str
    enabled: bool


class KeycloakAdminClient(Protocol):
    """The Keycloak Admin operations member onboarding needs."""

    async def create_user(
        self,
        *,
        email: str,
        tenant_id: UUID,
        display_name: str | None,
        email_verified: bool = False,
    ) -> KeycloakUser:
        """Create a realm user carrying the ``tenant_id`` attribute.

        ``email_verified`` defaults to ``False`` (the invite flow verifies via
        the set-password email). The setup wizard (Stream ACCT) passes ``True``
        so the first platform admin can log in immediately with a chosen
        password, no email round-trip.

        Raises :class:`KeycloakUserExistsError` on 409,
        :class:`KeycloakUnavailableError` on transport / 5xx.
        """

    async def send_setup_email(self, *, user_id: str, lifespan_s: int) -> None:
        """Send the native set-password / verify-email action link."""

    async def set_enabled(self, *, user_id: str, enabled: bool) -> None:
        """Enable/disable an account (member suspend / reactivate)."""

    async def reset_password(self, *, user_id: str, password: str, temporary: bool) -> None:
        """Set the user's password. ``temporary=True`` forces a change on next login."""

    async def delete_user(self, *, user_id: str) -> None:
        """Delete an account (invite revoke); a 404 is treated as success."""


def _split_name(display_name: str | None) -> tuple[str | None, str | None]:
    """Best-effort first/last split for Keycloak's name fields."""
    if not display_name:
        return None, None
    parts = display_name.strip().split(None, 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


class HttpKeycloakAdminClient:
    """Live Keycloak Admin REST client (httpx)."""

    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        token_provider: ServiceAccountTokenProvider,
        http: httpx.AsyncClient,
    ) -> None:
        self._admin = f"{base_url.rstrip('/')}/admin/realms/{realm}"
        self._token_provider = token_provider
        self._http = http

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._token_provider.bearer()}"}

    async def create_user(
        self,
        *,
        email: str,
        tenant_id: UUID,
        display_name: str | None,
        email_verified: bool = False,
    ) -> KeycloakUser:
        first, last = _split_name(display_name)
        body: dict[str, object] = {
            "username": email,
            "email": email,
            "enabled": True,
            "emailVerified": email_verified,
            "attributes": {"tenant_id": [str(tenant_id)]},
        }
        if first is not None:
            body["firstName"] = first
        if last is not None:
            body["lastName"] = last

        try:
            resp = await self._http.post(
                f"{self._admin}/users", json=body, headers=await self._auth_headers()
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"create_user request failed: {exc}") from exc

        if resp.status_code == 409:
            raise KeycloakUserExistsError(email)
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"create_user 5xx: HTTP {resp.status_code}")
        if resp.status_code not in (201, 204):
            raise KeycloakUnavailableError(
                f"create_user unexpected status: HTTP {resp.status_code}"
            )

        # Keycloak returns the new user id in the Location header's last segment.
        location = resp.headers.get("Location", "")
        user_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            raise KeycloakUnavailableError("create_user returned no Location id")
        return KeycloakUser(id=user_id, username=email, email=email, enabled=True)

    async def send_setup_email(self, *, user_id: str, lifespan_s: int) -> None:
        try:
            resp = await self._http.put(
                f"{self._admin}/users/{user_id}/execute-actions-email",
                params={"lifespan": lifespan_s},
                json=["UPDATE_PASSWORD", "VERIFY_EMAIL"],
                headers=await self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"send_setup_email request failed: {exc}") from exc
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"send_setup_email 5xx: HTTP {resp.status_code}")
        if resp.status_code not in (200, 204):
            raise KeycloakUnavailableError(
                f"send_setup_email unexpected status: HTTP {resp.status_code}"
            )

    async def set_enabled(self, *, user_id: str, enabled: bool) -> None:
        try:
            resp = await self._http.put(
                f"{self._admin}/users/{user_id}",
                json={"enabled": enabled},
                headers=await self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"set_enabled request failed: {exc}") from exc
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"set_enabled 5xx: HTTP {resp.status_code}")
        if resp.status_code not in (200, 204):
            raise KeycloakUnavailableError(
                f"set_enabled unexpected status: HTTP {resp.status_code}"
            )

    async def reset_password(self, *, user_id: str, password: str, temporary: bool) -> None:
        try:
            resp = await self._http.put(
                f"{self._admin}/users/{user_id}/reset-password",
                json={"type": "password", "value": password, "temporary": temporary},
                headers=await self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"reset_password request failed: {exc}") from exc
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"reset_password 5xx: HTTP {resp.status_code}")
        if resp.status_code not in (200, 204):
            raise KeycloakUnavailableError(
                f"reset_password unexpected status: HTTP {resp.status_code}"
            )

    async def delete_user(self, *, user_id: str) -> None:
        try:
            resp = await self._http.delete(
                f"{self._admin}/users/{user_id}", headers=await self._auth_headers()
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailableError(f"delete_user request failed: {exc}") from exc
        if resp.status_code == 404:
            return  # already gone — idempotent
        if resp.status_code >= 500:
            raise KeycloakUnavailableError(f"delete_user 5xx: HTTP {resp.status_code}")
        if resp.status_code not in (200, 204):
            raise KeycloakUnavailableError(
                f"delete_user unexpected status: HTTP {resp.status_code}"
            )
