"""Audit-context middleware — Stream B.1 (retired by Stream C.1).

Originally this middleware *resolved* the per-request tenant from
``X-Expert-Work-Tenant`` / ``X-Expert-Work-Actor`` headers under ``auth_mode=dev``.
Stream C.1 deprecated that header-trust path: :class:`AuthMiddleware`
now populates ``request.state.principal`` from a verified JWT and the
job here shrinks to **projecting** that principal into the ctxvar
consumed by the structured logger.

For auth-exempt paths (``/healthz``, ``/metrics``) :class:`AuthMiddleware`
leaves no principal on the request; this middleware falls back to the
configured dev defaults so log lines from those endpoints still carry a
deterministic tenant value.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from expert_work.common.context import reset_current_tenant, set_current_tenant
from expert_work.protocol import Principal

logger = logging.getLogger("expert_work.control_plane.audit_context")


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Project :attr:`request.state.principal` into the structured-log ctxvar."""

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
        principal: Principal | None = getattr(request.state, "principal", None)

        if principal is not None:
            tenant_id = principal.tenant_id
            actor_id = principal.subject_id
        else:
            tenant_id = self._default_tenant_id
            actor_id = self._default_actor_id

        # Maintain the legacy ``request.state`` aliases for handlers that
        # have not yet migrated to reading ``principal`` directly.
        request.state.tenant_id = tenant_id
        request.state.actor_id = actor_id

        token = set_current_tenant(tenant_id)
        try:
            return await call_next(request)
        finally:
            reset_current_tenant(token)
