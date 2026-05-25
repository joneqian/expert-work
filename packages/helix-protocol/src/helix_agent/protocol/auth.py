"""Auth-domain Pydantic schemas — Stream C.1.

Two stable wire types:

* :class:`Principal` — the resolved identity attached to ``request.state`` by
  the control plane's ``AuthMiddleware``. Downstream code (RBAC, audit
  emitter, RLS session bootstrap) reads only this object.
* :class:`JWTClaims` — the verified-and-parsed shape of an inbound JWT;
  produced by :class:`control_plane.auth.JWTVerifier`. ``Principal`` is
  built from it via :meth:`Principal.from_jwt_claims`.

Three other auth methods (API key, mTLS) are scoped to Stream C.2 / C.3 and
attach to the same :class:`Principal` envelope.

See ``docs/streams/STREAM-C-DESIGN.md`` §2.3 / ADR C-2.
"""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AuthMethod = Literal["jwt", "api_key", "mtls"]
SubjectType = Literal["user", "service_account", "service"]


class JWTClaims(BaseModel):
    """Verified-and-parsed JWT claims (issuer / signature already checked)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    iss: str
    sub: str
    aud: tuple[str, ...]
    exp: int
    iat: int | None = None
    jti: str | None = None
    tenant_id: UUID
    sub_type: SubjectType = "user"
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    allowed_tenants: tuple[UUID, ...] = ()


#: Stream N — sentinel for ``Principal.allowed_tenants`` indicating
#: cross-tenant access (any tenant). Only system_admin (Stream N) and
#: certain mTLS internal service principals should carry this value.
ALL_TENANTS: Literal["*"] = "*"


class Principal(BaseModel):
    """Resolved request identity. Single source of truth across middleware.

    Producers:
      * ``JWTVerifier`` → :meth:`from_jwt_claims`   (Stream C.1)
      * ``ApiKeyVerifier`` → :meth:`from_api_key`   (Stream C.3)
      * ``MtlsVerifier`` → :meth:`from_peer_cert`   (Stream C.2)

    Stream N:``allowed_tenants`` extended to ``tuple[UUID, ...] | Literal["*"]``;
    new ``is_system_admin: bool`` field is populated by ``AuthMiddleware`` after
    the verifier returns, by looking up a platform-scope role binding for the
    subject. ``tenant_id`` remains required (non-None) — a system_admin user's
    JWT still carries a "home tenant"; cross-tenant capability comes from
    ``allowed_tenants="*"`` and the per-route ``ensure_tenant_scope`` logic
    introduced in N.3.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    subject_id: str
    subject_type: SubjectType
    tenant_id: UUID
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    auth_method: AuthMethod = "jwt"
    # Stream N:literal "*" means "any tenant" (cross-tenant capability).
    allowed_tenants: tuple[UUID, ...] | Literal["*"] = ()
    is_system_admin: bool = False  # Stream N
    jti: str | None = Field(default=None, description="JWT id — None for non-JWT methods")

    @classmethod
    def from_jwt_claims(cls, claims: JWTClaims) -> Self:
        # ``is_system_admin`` defaults to False here;``AuthMiddleware``
        # augments the Principal after this call by querying the
        # role-binding store. See ``control_plane.auth.system_admin``.
        return cls(
            subject_id=claims.sub,
            subject_type=claims.sub_type,
            tenant_id=claims.tenant_id,
            roles=claims.roles,
            scopes=claims.scopes,
            auth_method="jwt",
            allowed_tenants=claims.allowed_tenants or (claims.tenant_id,),
            is_system_admin=False,
            jti=claims.jti,
        )

    def as_system_admin(self) -> Self:
        """Return a copy with ``is_system_admin=True`` and ``allowed_tenants="*"`` — Stream N.

        Used by ``AuthMiddleware`` after a platform-scope role binding
        lookup succeeds for this subject. ``tenant_id`` and other fields
        are preserved (the user's home tenant remains their default
        scope until they explicitly request another via the ``tenant_id``
        query parameter at the route layer).
        """
        return self.model_copy(update={"is_system_admin": True, "allowed_tenants": ALL_TENANTS})
