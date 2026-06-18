"""``GET /v1/me`` — identity introspection for UI clients.

Stream H.1b PR 2a. The Admin UI used to decode the JWT in-browser to
discover ``tenant_id`` / ``is_system_admin`` / ``subject_type``. That
worked for OIDC tokens but produced no information for opaque helix
API keys (the UI saw only the bearer prefix). Stream N also adds a
server-side ``is_system_admin`` augmentation that the JWT itself does
not carry — so the JWT-local decode is no longer authoritative.

``GET /v1/me`` returns the resolved :class:`Principal` straight from
``request.state``. The middleware stack — AuthMiddleware (JWT / API
key / mTLS) + ``resolve_system_admin`` — has already done the
verification, so the route is a pure projection.

Response shape (envelope-wrapped to match the rest of ``/v1/*``)::

    {
      "success": true,
      "data": {
        "subject_id": "...",
        "subject_type": "user" | "service_account" | "service",
        "tenant_id": "<uuid>",
        "auth_method": "jwt" | "api_key" | "mtls",
        "roles": ["operator", ...],
        "scopes": ["read", ...],
        "is_system_admin": false,
        "allowed_tenants": ["<uuid>", ...] | "*"
      },
      "error": null
    }

No audit emit — read-only introspection of the caller's own identity
is not a privacy-relevant event (the request's auth check already
audited login_success / login_failed).
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from helix_agent.protocol import Principal

logger = logging.getLogger("helix.control_plane.api.me")


class MeResponse(BaseModel):
    """Wire shape returned inside the envelope's ``data`` field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str
    subject_type: Literal["user", "service_account", "service"]
    tenant_id: UUID
    # Stream ACCT — OIDC email for the user menu (JWT only; None for API key /
    # mTLS). The UI shows this instead of the bare subject UUID.
    email: str | None
    auth_method: Literal["jwt", "api_key", "mtls"]
    roles: tuple[str, ...]
    scopes: tuple[str, ...]
    is_system_admin: bool
    # ``"*"`` (cross-tenant) is reserved for system_admin + a small set
    # of internal mTLS principals. Concrete tenants are wired through as
    # a list. UI uses this to decide whether the TenantSwitcher should
    # offer "All tenants".
    allowed_tenants: tuple[UUID, ...] | Literal["*"]

    @classmethod
    def from_principal(cls, principal: Principal) -> MeResponse:
        return cls(
            subject_id=principal.subject_id,
            subject_type=principal.subject_type,
            tenant_id=principal.tenant_id,
            email=principal.email,
            auth_method=principal.auth_method,
            roles=principal.roles,
            scopes=principal.scopes,
            is_system_admin=principal.is_system_admin,
            allowed_tenants=principal.allowed_tenants,
        )


def build_me_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["me"])

    @router.get("/me", response_model=None)
    async def get_me(request: Request) -> dict[str, object]:
        principal: Principal = request.state.principal
        return {
            "success": True,
            "data": MeResponse.from_principal(principal).model_dump(mode="json"),
            "error": None,
        }

    return router
