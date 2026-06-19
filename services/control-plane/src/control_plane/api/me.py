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
        "email": "<str>" | null,
        "auth_method": "jwt" | "api_key" | "mtls",
        "roles": ["operator", ...],
        "scopes": ["read", ...],
        "is_system_admin": false,
        "home_is_platform": false,
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
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from control_plane.settings import Settings
from helix_agent.protocol import Principal


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


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
    # Stream ACCT — ``True`` when the caller's home tenant is the synthetic
    # platform tenant (the storage home for /setup-provisioned system_admins,
    # not a real customer tenant). The UI hides this tenant from the
    # TenantSwitcher: the platform level is the ``"*"`` scope, not a peer row.
    # A dual-role admin (real-tenant member granted platform scope) homes to
    # their company tenant ⇒ ``False`` ⇒ their home stays selectable.
    home_is_platform: bool
    # ``"*"`` (cross-tenant) is reserved for system_admin + a small set
    # of internal mTLS principals. Concrete tenants are wired through as
    # a list. UI uses this to decide whether the TenantSwitcher should
    # offer "All tenants".
    allowed_tenants: tuple[UUID, ...] | Literal["*"]

    @classmethod
    def from_principal(cls, principal: Principal, *, platform_tenant_id: UUID) -> MeResponse:
        return cls(
            subject_id=principal.subject_id,
            subject_type=principal.subject_type,
            tenant_id=principal.tenant_id,
            email=principal.email,
            auth_method=principal.auth_method,
            roles=principal.roles,
            scopes=principal.scopes,
            is_system_admin=principal.is_system_admin,
            home_is_platform=principal.tenant_id == platform_tenant_id,
            allowed_tenants=principal.allowed_tenants,
        )


def build_me_router() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["me"])

    @router.get("/me", response_model=None)
    async def get_me(
        request: Request,
        settings: Annotated[Settings, Depends(_get_settings)],
    ) -> dict[str, object]:
        principal: Principal = request.state.principal
        return {
            "success": True,
            "data": MeResponse.from_principal(
                principal, platform_tenant_id=settings.platform_tenant_id
            ).model_dump(mode="json"),
            "error": None,
        }

    return router
