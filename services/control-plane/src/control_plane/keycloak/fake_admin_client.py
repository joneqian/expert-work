"""In-memory ``KeycloakAdminClient`` — Stream R (Mini-ADR R-1).

Used both as the unit-test double and as the dev/CI runtime client when
``keycloak_enabled`` is false, so the full onboarding flow runs without a live
Keycloak. Account ids are derived deterministically from the email so a test
can predict them; ``raise_exists_for`` lets a test force the 409 path.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from uuid import UUID

from control_plane.keycloak.admin_client import KeycloakUser
from control_plane.keycloak.errors import KeycloakUserExistsError


@dataclass
class _StoredUser:
    user: KeycloakUser
    tenant_id: UUID
    emails_sent: int = 0


@dataclass
class FakeKeycloakAdminClient:
    """In-memory stand-in honouring the :class:`KeycloakAdminClient` protocol."""

    users: dict[str, _StoredUser] = field(default_factory=dict)
    #: Emails (lower-cased) that ``create_user`` should reject with 409.
    raise_exists_for: set[str] = field(default_factory=set)

    @staticmethod
    def _deterministic_id(email: str) -> str:
        digest = hashlib.sha256(email.lower().encode()).digest()
        return str(uuid.UUID(bytes=digest[:16]))

    async def create_user(
        self, *, email: str, tenant_id: UUID, display_name: str | None
    ) -> KeycloakUser:
        if email.lower() in self.raise_exists_for:
            raise KeycloakUserExistsError(email)
        if any(s.user.email.lower() == email.lower() for s in self.users.values()):
            raise KeycloakUserExistsError(email)
        user = KeycloakUser(
            id=self._deterministic_id(email),
            username=email,
            email=email,
            enabled=True,
        )
        self.users[user.id] = _StoredUser(user=user, tenant_id=tenant_id)
        return user

    async def send_setup_email(self, *, user_id: str, lifespan_s: int) -> None:
        stored = self.users.get(user_id)
        if stored is not None:
            stored.emails_sent += 1

    async def set_enabled(self, *, user_id: str, enabled: bool) -> None:
        stored = self.users.get(user_id)
        if stored is not None:
            stored.user = KeycloakUser(
                id=stored.user.id,
                username=stored.user.username,
                email=stored.user.email,
                enabled=enabled,
            )

    async def delete_user(self, *, user_id: str) -> None:
        self.users.pop(user_id, None)  # idempotent
