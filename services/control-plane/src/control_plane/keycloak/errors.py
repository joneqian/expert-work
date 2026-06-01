"""Keycloak Admin client error hierarchy — Stream R (Mini-ADR R-1)."""

from __future__ import annotations


class KeycloakError(Exception):
    """Base for all Keycloak Admin client failures."""


class KeycloakUnavailableError(KeycloakError):
    """Keycloak could not be reached / returned a 5xx / timed out.

    The DB-first compensation (Mini-ADR R-4) maps this to a 502 and leaves the
    local row in a re-tryable state — ``resend`` finishes the Keycloak side.
    """


class KeycloakAuthError(KeycloakError):
    """The service-account token grant was rejected (bad client secret / config).

    A misconfiguration, not a user error — maps to 500.
    """


class KeycloakUserExistsError(KeycloakError):
    """``create_user`` got a 409 — the email/username already exists in the realm.

    ``duplicateEmailsAllowed`` is false, so one email maps to at most one
    account realm-wide; helix never auto-reuses it across tenants (Mini-ADR
    R-11). Maps to 409 ``MEMBER_KEYCLOAK_CONFLICT``.
    """

    def __init__(self, email: str) -> None:
        super().__init__(f"keycloak user already exists: {email!r}")
        self.email = email
