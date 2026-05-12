"""Audit-context middleware — Stream B.1, ADR B-5.

Resolves a ``tenant_id`` / ``actor_id`` per request:

* In ``HELIX_AGENT_AUTH_MODE=dev`` we trust ``X-Helix-Tenant`` /
  ``X-Helix-Actor`` headers. Missing or malformed tenant → fall back to
  the configured dev defaults.
* In ``HELIX_AGENT_AUTH_MODE=prod`` the control-plane refuses to boot
  (handled in :func:`control_plane.app.create_app`); this middleware
  therefore only runs in dev mode. The fail-fast guard is what protects
  prod traffic until C.1 OIDC middleware ships.

Tenant id is published into :mod:`helix_agent.common.context` so the
structured logger and any future audit emitter can pull it without
threading the request object through their call site.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from helix_agent.common.context import reset_current_tenant, set_current_tenant

TENANT_HEADER = "X-Helix-Tenant"
ACTOR_HEADER = "X-Helix-Actor"

logger = logging.getLogger("helix.control_plane.audit_context")


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Inject ``tenant_id`` / ``actor_id`` into request state + ctxvars."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_tenant_id: UUID,
        default_actor_id: str,
    ) -> None:
        super().__init__(app)
        self._default_tenant_id = default_tenant_id
        self._default_actor_id = default_actor_id

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        tenant_id = self._resolve_tenant(request)
        actor_id = request.headers.get(ACTOR_HEADER) or self._default_actor_id

        request.state.tenant_id = tenant_id
        request.state.actor_id = actor_id

        token = set_current_tenant(tenant_id)
        try:
            return await call_next(request)
        finally:
            reset_current_tenant(token)

    def _resolve_tenant(self, request: Request) -> UUID:
        raw = request.headers.get(TENANT_HEADER)
        if not raw:
            return self._default_tenant_id
        try:
            return UUID(raw)
        except ValueError:
            logger.debug(
                "audit_context.tenant_header_malformed",
                extra={"header_value": raw[:64]},
            )
            return self._default_tenant_id
